import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'patches/patch_apk.sh'
STUB = '''#!/usr/bin/env python3
import os
from pathlib import Path
import sys
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['TRACE'], 'a') as f:
    f.write(name + '\\n')
if name == 'java':
    target = Path(args[args.index('-o') + 1])
    assert 'd' in args and '-r' not in args
    target.mkdir()
    (target / 'apktool.yml').write_text('version: 3.0.2')
elif name == 'apk-mitm':
    source = Path(args[-1])
    assert source.is_dir()
    assert (source / 'apktool.yml').exists()
    assert (source / 'fingerprint-applied').exists()
    mode = os.environ.get('MITM_MODE')
    if mode == 'fail':
        sys.exit(2)
    if mode != 'missing':
        source.with_name(source.stem + '-patched.apk').write_bytes(b'final')
elif name == 'apksigner':
    assert Path(args[-1]).read_bytes() == b'final'
    if os.environ.get('VERIFY_FAIL'):
        sys.exit(1)
'''
PRIVATE = '''import os
from pathlib import Path
import sys
assert Path(sys.argv[2]).read_bytes() == b'original'
assert 'FINGERPRINT_PATCH' not in os.environ
with open(os.environ['TRACE'], 'a') as f:
    f.write('fingerprint\\n')
Path(sys.argv[1], 'fingerprint-applied').touch()
'''


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        tools = self.root / 'bin'
        tools.mkdir()
        for name in ('java', 'apk-mitm', 'apksigner', 'zipalign'):
            p = tools / name
            p.write_text(STUB)
            p.chmod(0o755)
        self.source = self.root / 'original.apk'
        self.source.write_bytes(b'original')
        self.output = self.root / 'output.apk'
        self.trace = self.root / 'trace'
        self.env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                        APKTOOL_JAR='/fake.jar', TRACE=str(self.trace), FINGERPRINT_PATCH=PRIVATE)
        self.env.pop('FINGERPRINT_PATCH_FILE', None)

    def run_build(self):
        return subprocess.run(['bash', str(SCRIPT), str(self.source), str(self.output)],
                              env=self.env, capture_output=True, text=True)

    def test_fingerprint_before_mitm_and_verification(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.trace.read_text().splitlines(),
                         ['java', 'fingerprint', 'apk-mitm', 'apksigner', 'zipalign'])
        self.assertEqual(self.output.read_bytes(), b'final')
        self.assertEqual(self.source.read_bytes(), b'original')

    def test_private_error_is_hidden_and_stops_pipeline(self):
        self.env['FINGERPRINT_PATCH'] = 'raise RuntimeError("PRIVATE-SENTINEL")'
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('PRIVATE-SENTINEL', result.stdout + result.stderr)
        self.assertEqual(self.trace.read_text().splitlines(), ['java'])
        self.assertFalse(self.output.exists())

    def test_mitm_failure_or_missing_output_stops_pipeline(self):
        for mode in ('fail', 'missing'):
            with self.subTest(mode=mode):
                self.env['MITM_MODE'] = mode
                result = self.run_build()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())

    def test_invalid_signature_is_not_published(self):
        self.env['VERIFY_FAIL'] = '1'
        self.assertNotEqual(self.run_build().returncode, 0)
        self.assertFalse(self.output.exists())

    def test_external_private_file(self):
        private = self.root / 'private.py'
        private.write_text(PRIVATE)
        self.env.pop('FINGERPRINT_PATCH')
        self.env['FINGERPRINT_PATCH_FILE'] = str(private)
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
