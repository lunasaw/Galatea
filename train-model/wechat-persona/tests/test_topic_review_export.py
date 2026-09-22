from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona._common import digest, file_digest
from wechat_persona.topic_context import TopicContractError
from wechat_persona.topic_review_export import export_review


class TopicReviewExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pilot = self.root / 'pilot'
        self.review = self.root / 'review'
        self.output = self.root / 'output'
        self.pilot.mkdir(); self.review.mkdir()
        candidates = [{
            'sample_id': f'sample-{index}', 'candidate_sha256': str(index) * 64,
            'day': '2024-05-20',
            'messages': [{'role':'system','content':'fixture'},
                         {'role':'user','content':'<script>not executable</script>'},
                         {'role':'assistant','content':'fixture reply'}],
        } for index in range(3)]
        (self.pilot / 'candidates.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in candidates))
        self.write(self.pilot/'manifest.json', {'output_digests':{
            'candidates.jsonl':file_digest(self.pilot/'candidates.jsonl')}})
        decisions = [{
            'sample_id':row['sample_id'],'candidate_sha256':row['candidate_sha256'],
            'status':status,'reason':'usable_reply' if status=='keep' else 'uncertain',
            'confidence':0.9,'review_kind':'machine',
        } for row,status in zip(candidates,['keep','reject','uncertain'])]
        self.write(self.review/'decisions.json',{
            'identity':{'pilot_manifest_sha256':file_digest(self.pilot/'manifest.json')},
            'decisions':decisions})
        self.write(self.review/'report.json',{'status':'complete','decisions_sha256':digest(decisions)})

    def write(self,path,value):
        path.write_text(json.dumps(value),encoding='utf-8')

    def run_export(self):
        return export_review(self.pilot,self.review,self.output,self.root)

    def test_export_keeps_only_selected_rows_and_escapes_page_content(self):
        result = self.run_export()
        directory = Path(result['output_dir'])
        self.assertEqual(result['kept_count'],1)
        self.assertEqual(len((directory/'train.draft.jsonl').read_text().splitlines()),1)
        self.assertNotIn('<script>',(directory/'review.html').read_text())
        self.assertIn('&lt;script&gt;', (directory/'review.html').read_text())
        manifest = json.loads((directory/'manifest.json').read_text())
        self.assertFalse(manifest['formal_training_eligible'])
        self.assertFalse(manifest['human_review_completed'])
        self.assertEqual(self.run_export()['status'],'already_built')

    def test_incomplete_review_cannot_export(self):
        report = json.loads((self.review/'report.json').read_text())
        self.write(self.review/'report.json',{**report,'status':'incomplete'})
        with self.assertRaisesRegex(TopicContractError,'incomplete'):
            self.run_export()

    def test_modified_candidate_file_cannot_export(self):
        with (self.pilot/'candidates.jsonl').open('a') as handle:
            handle.write('{}\n')
        with self.assertRaisesRegex(TopicContractError,'candidate file changed'):
            self.run_export()

    def test_existing_export_tampering_cannot_be_reused(self):
        result = self.run_export()
        (Path(result['output_dir'])/'train.draft.jsonl').write_text('{}\n')
        with self.assertRaisesRegex(TopicContractError,'existing export digest'):
            self.run_export()


if __name__ == '__main__':
    unittest.main()
