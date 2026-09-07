"""Version-specific S3 reads with bounded memory and SHA-256 verification."""
import hashlib


class S3Objects:
    def __init__(self, client):
        self.client = client

    def verify_metadata(self, ref):
        result = self.client.head_object(Bucket=ref['bucket'], Key=ref['key'], VersionId=ref['version_id'])
        return result.get('VersionId') == ref['version_id'] and result.get('ContentLength') == ref['size_bytes']

    def verify(self, ref):
        result = self.client.get_object(Bucket=ref['bucket'], Key=ref['key'], VersionId=ref['version_id'])
        stream = result['Body']
        try:
            if result.get('VersionId') != ref['version_id'] or result.get('ContentLength') != ref['size_bytes']:
                return False
            h, total = hashlib.sha256(), 0
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > ref['size_bytes']:
                    return False
                h.update(chunk)
            return total == ref['size_bytes'] and h.hexdigest() == ref['sha256']
        finally:
            stream.close()
