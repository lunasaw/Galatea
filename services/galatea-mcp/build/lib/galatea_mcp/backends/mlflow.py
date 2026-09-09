"""Tracking and Artifact APIs only. Require server-side artifact proxying."""
from __future__ import annotations
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from ..errors import DomainError
from ..projects import relative_path, file_sha


class MLflowEvidence:
    def __init__(self, client, download_root: Path, *, history_client=None):
        self.client, self.download_root = client, download_root.resolve()
        self.history_client = history_client
        self.download_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def get_run(self, run_id):
        run = self.client.get_run(run_id)
        info, data = run.info, run.data
        tags = data.tags
        return {'run_id': info.run_id, 'experiment_id': info.experiment_id, 'status': info.status,
                'metrics': data.metrics, 'project_id': tags.get('galatea.project'),
                'campaign_id': tags.get('galatea.campaign'), 'operation_id': tags.get('galatea.operation'),
                'role': tags.get('galatea.role')}

    def for_operation(self, project, campaign, operation):
        # IDs have already passed the strict identifier schema; no model-provided filter language.
        filter_string = f"tags.`galatea.operation` = '{operation['operation_id']}'"
        runs = self.client.search_runs([project.experiment_id], filter_string=filter_string,
                                       max_results=2, order_by=['attributes.start_time ASC'])
        return [self.get_run(r.info.run_id) for r in runs]

    def history_page(self, run_id, metric, *, limit=50, cursor=None):
        if self.history_client is None:
            raise DomainError('tracking-history-client-required')
        params = {'run_id': run_id, 'metric_key': metric, 'max_results': limit}
        if cursor:
            params['page_token'] = cursor
        response = self.history_client.get('/api/2.0/mlflow/metrics/get-history', params=params)
        response.raise_for_status()
        data = response.json()
        items = data.get('metrics', [])
        if not isinstance(items, list) or len(items) > limit:
            raise DomainError('invalid-metric-page')
        return {'items': items, 'next_cursor': data.get('next_page_token') or None}

    def _download(self, run_id, path, limit, consume):
        path = str(relative_path(path))
        info = self.client.get_run(run_id).info
        uri = urlsplit(info.artifact_uri)
        if uri.scheme != 'mlflow-artifacts' or uri.netloc:
            raise DomainError('artifact-proxy-required')
        entries = self.client.list_artifacts(run_id, str(PurePosixPath(path).parent))
        target = next((f for f in entries if f.path == path and not f.is_dir), None)
        if target is None or type(target.file_size) is not int or not 0 <= target.file_size <= limit:
            raise DomainError('artifact-size-or-missing')
        with tempfile.TemporaryDirectory(prefix='galatea-artifact-', dir=self.download_root) as tmp:
            downloaded = Path(self.client.download_artifacts(run_id, path, dst_path=tmp))
            root = Path(tmp).resolve()
            if (not downloaded.resolve().is_relative_to(root) or downloaded.is_symlink()
                    or any(parent.is_symlink() for parent in downloaded.parents if parent.is_relative_to(root))
                    or not downloaded.is_file() or downloaded.stat().st_size != target.file_size):
                raise DomainError('artifact-integrity')
            return consume(downloaded)

    def read_artifact(self, run_id, path, max_bytes):
        return self._download(run_id, path, max_bytes, lambda p: p.read_bytes())

    def artifact_digest(self, run_id, path, expected, max_bytes):
        return self._download(run_id, path, max_bytes,
                              lambda p: p.stat().st_size == max_bytes and file_sha(p) == expected)
