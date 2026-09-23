import base64
import copy
import hashlib
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from codex_agent.provenance import validate_statement
from codex_agent.stage0 import GateError


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        sha=hashlib.sha512(b'archive').hexdigest()
        self.metadata={'name':'@openai/codex','version':'0.153.4-linux-x64','dist':{
            'integrity':'sha512-'+base64.b64encode(bytes.fromhex(sha)).decode()}}
        self.statement={'predicateType':'https://slsa.dev/provenance/v1', 'subject':[
            {'name':'pkg:npm/%40openai/codex@0.153.4-linux-x64','digest':{'sha512':sha}}],
            'predicate':{'buildDefinition':{'externalParameters':{'workflow':{
                'repository':'https://github.com/openai/codex','ref':'refs/tags/rust-v0.153.4',
                'path':'.github/workflows/rust-release.yml'}},
                'resolvedDependencies':[{'uri':'git+https://github.com/openai/codex@refs/tags/rust-v0.153.4',
                                         'digest':{'gitCommit':'a'*40}}]}}}

    def test_source_commit_is_bound_to_exact_package_subject_and_release_workflow(self):
        self.assertEqual(validate_statement(self.metadata,self.statement,'0.153.4')['source_commit'],'a'*40)

    def test_different_repository_subject_digest_or_source_is_rejected(self):
        for mutation in (
            lambda s:s['subject'][0]['digest'].update(sha512='b'*128),
            lambda s:s['subject'][0].update(name='pkg:npm/other@0.153.4-linux-x64'),
            lambda s:s['predicate']['buildDefinition']['externalParameters']['workflow'].update(repository='https://github.com/other/codex'),
            lambda s:s['predicate']['buildDefinition']['externalParameters']['workflow'].update(ref='refs/heads/main'),
            lambda s:s['predicate']['buildDefinition']['resolvedDependencies'].clear(),
        ):
            statement=copy.deepcopy(self.statement);mutation(statement)
            with self.assertRaises(GateError):
                validate_statement(self.metadata,statement,'0.153.4')
