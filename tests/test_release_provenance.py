"""Adversarial policy fixtures; cryptographic verification stays in trusted gh."""
import copy
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import signal
import stat
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
import zipfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("dots_ts_provenance_test", str(ROOT / "dots-ts"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
tool = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(tool)


def payload_fixture(version="0.8.0"):
    payload = {name: ("# " + name + "\n").encode() for name in tool.CORE_FILES}
    payload[tool.MANIFEST] = json.dumps({"schema_version": 1, "package_version": version,
        "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in payload.items()}}).encode()
    return payload


def evidence_fixture(payload):
    version = json.loads(payload[tool.MANIFEST])["package_version"]
    metadata = {"schema_version": 1, "repository": tool.UPDATE_REPOSITORY,
                "workflow": tool.PROVENANCE_WORKFLOW, "tag": "v" + version,
                "source_commit": "a" * 40,
                "manifest_sha256": hashlib.sha256(payload[tool.MANIFEST]).hexdigest()}
    evidence = {tool.PROVENANCE_METADATA: json.dumps(metadata).encode(), tool.PROVENANCE_BUNDLE: b'{"fixture":"not a signature"}'}
    ref = "refs/tags/" + metadata["tag"]
    certificate = {"issuer": "https://token.actions.githubusercontent.com",
        "subjectAlternativeName": tool.UPDATE_WEB + "/" + tool.PROVENANCE_WORKFLOW + "@" + ref,
        "sourceRepositoryURI": tool.UPDATE_WEB, "sourceRepositoryIdentifier": tool.PROVENANCE_REPOSITORY_ID,
        "sourceRepositoryOwnerURI": "https://github.com/abruption", "sourceRepositoryOwnerIdentifier": tool.PROVENANCE_OWNER_ID,
        "sourceRepositoryRef": ref, "sourceRepositoryDigest": metadata["source_commit"],
        "buildSignerDigest": metadata["source_commit"], "runnerEnvironment": "github-hosted"}
    statement = {"_type": "https://in-toto.io/Statement/v1", "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [{"name": tool.MANIFEST, "digest": {"sha256": metadata["manifest_sha256"]}}],
        "predicate": {"buildDefinition": {"externalParameters": {"workflow": {
            "repository": tool.UPDATE_WEB, "path": tool.PROVENANCE_WORKFLOW, "ref": ref}},
            "resolvedDependencies": [{"uri": "git+" + tool.UPDATE_WEB + "@" + ref,
                "digest": {"gitCommit": metadata["source_commit"]}}]}}}
    verified = {"verificationResult": {"signature": {"certificate": certificate},
        "statement": statement, "verifiedTimestamps": [{"type": "fixture"}]}}
    return metadata, evidence, [verified]


class GhOutputContractTests(unittest.TestCase):
    """Real public stdout shape; adapted positives are not signed evidence."""
    FIXTURE = ROOT / "tests/fixtures/gh-attestation-2.103.3-actions-attest-demo.json"
    SHA256 = "f46563c3a1ffb835dec870a01e6605960350ae9fdef073489fe5a9b33f03a324"

    def test_unmodified_public_stdout_has_the_real_nested_contract(self):
        raw = self.FIXTURE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.SHA256)
        result = json.loads(raw)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]), {"attestation", "verificationResult"})
        verified = result[0]["verificationResult"]
        # This is one signature-summary OBJECT containing a certificate,
        # not a list of signatures and not a flattened certificate wrapper.
        self.assertIsInstance(verified["signature"], dict)
        certificate = verified["signature"]["certificate"]
        self.assertIsInstance(certificate, dict)
        self.assertNotIn("certificate", result[0])
        for field in ("sourceRepositoryIdentifier", "sourceRepositoryOwnerIdentifier",
                      "sourceRepositoryRef", "sourceRepositoryDigest", "buildSignerDigest"):
            self.assertIsInstance(certificate[field], str)
        self.assertEqual(certificate["sourceRepositoryURI"], "https://github.com/actions/attest-demo")
        self.assertIsInstance(verified["verifiedTimestamps"], list)
        self.assertTrue(verified["verifiedTimestamps"])
        statement = verified["statement"]
        self.assertIsInstance(statement, dict)
        self.assertEqual(statement["predicateType"], "https://slsa.dev/provenance/v1")
        definition = statement["predicate"]["buildDefinition"]
        self.assertEqual(set(definition["externalParameters"]["workflow"]), {"path", "ref", "repository"})
        self.assertIsInstance(definition["resolvedDependencies"], list)
        self.assertEqual(definition["resolvedDependencies"][0]["digest"]["gitCommit"],
                         certificate["sourceRepositoryDigest"])

    def test_unmodified_upstream_publisher_is_not_this_packages_evidence(self):
        payload = payload_fixture()
        metadata, _, _ = evidence_fixture(payload)
        # The real sample has an unauthorized publisher/workflow/ref for this
        # package. Do not relabel its valid upstream signature as our proof.
        with self.assertRaisesRegex(tool.ProvenanceError, "provenance_policy_mismatch"):
            tool.provenance_certificate_result(self.FIXTURE.read_bytes(), metadata,
                                               hashlib.sha256(payload[tool.MANIFEST]).hexdigest())

    def test_policy_values_can_be_adapted_without_changing_the_real_wrapper(self):
        original = json.loads(self.FIXTURE.read_bytes())
        synthetic = copy.deepcopy(original)
        payload = payload_fixture()
        metadata, _, _ = evidence_fixture(payload)
        digest = hashlib.sha256(payload[tool.MANIFEST]).hexdigest()
        ref = "refs/tags/" + metadata["tag"]
        verified = synthetic[0]["verificationResult"]
        certificate = verified["signature"]["certificate"]
        # Change only values consumed by the post-gh policy. The signature,
        # certificate bytes and DSSE statement inside attestation.bundle stay
        # unmodified and NO LONGER match these adapted summaries. This is a
        # parser unit case after mocked verifier success, NOT crypto-valid
        # mutated evidence and never input to the real gh verifier.
        certificate.update({
            "subjectAlternativeName": tool.UPDATE_WEB + "/" + tool.PROVENANCE_WORKFLOW + "@" + ref,
            "sourceRepositoryURI": tool.UPDATE_WEB,
            "sourceRepositoryIdentifier": tool.PROVENANCE_REPOSITORY_ID,
            "sourceRepositoryOwnerURI": "https://github.com/abruption",
            "sourceRepositoryOwnerIdentifier": tool.PROVENANCE_OWNER_ID,
            "sourceRepositoryRef": ref, "sourceRepositoryDigest": metadata["source_commit"],
            "buildSignerDigest": metadata["source_commit"],
        })
        verified["statement"]["subject"][0]["digest"]["sha256"] = digest
        definition = verified["statement"]["predicate"]["buildDefinition"]
        definition["externalParameters"]["workflow"].update({
            "repository": tool.UPDATE_WEB, "path": tool.PROVENANCE_WORKFLOW, "ref": ref})
        definition["resolvedDependencies"][0].update({
            "uri": "git+" + tool.UPDATE_WEB + "@" + ref,
            "digest": {"gitCommit": metadata["source_commit"]}})
        self.assertEqual(synthetic[0]["attestation"], original[0]["attestation"])
        self.assertEqual(set(synthetic[0]), set(original[0]))
        self.assertEqual(set(verified), set(original[0]["verificationResult"]))
        report = tool.provenance_certificate_result(json.dumps(synthetic).encode(), metadata, digest)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["manifest_sha256"], digest)
        # The immutable captured fixture remains byte-for-byte original.
        self.assertEqual(hashlib.sha256(self.FIXTURE.read_bytes()).hexdigest(), self.SHA256)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="provenance-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        if sys.platform == "darwin":
            # Model only the host ancestry outside the private fixture as
            # protected: macOS external test volumes can be group-writable.
            # The product deliberately rejects those real paths. All leaf and
            # fixture-subtree permission/UID checks below remain exercised;
            # Linux CI uses its actual /tmp ancestry without this harness.
            ancestors = set(self.root.parents)
            actual_lstat = Path.lstat
            def protected_host_ancestry(path):
                info = actual_lstat(path)
                if path in ancestors and info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
                    values = list(info); values[stat.ST_MODE] &= ~0o022
                    return os.stat_result(values)
                return info
            patcher = mock.patch.object(Path, "lstat", protected_host_ancestry)
            patcher.start(); self.addCleanup(patcher.stop)
        self.trusted_root = self.root / "trusted-root.jsonl"
        self.trusted_root.write_text('{"fixture":"independently supplied root"}\n')
        self.trusted_root.chmod(0o600)
        self.payload = payload_fixture()
        self.metadata, self.evidence, self.verified = evidence_fixture(self.payload)

    def verify(self, result=None, **kwargs):
        with mock.patch.object(tool, "provenance_gh", return_value="/trusted/gh"), \
                mock.patch.object(tool, "provenance_run", side_effect=[
                    (0, b"gh version 2.103.3 (2026-10-26)\n"),
                    (0, json.dumps(self.verified if result is None else result).encode())]) as runner:
            report = tool.provenance_verify_payload(self.payload, evidence=self.evidence,
                provenance_root=self.trusted_root, **kwargs)
        return report, runner

    def test_authorized_verified_result_and_fixed_policy_flags(self):
        with mock.patch.dict(os.environ, {"GH_TOKEN": "PRIVATE", "HTTPS_PROXY": "http://PRIVATE.invalid",
                                          "AWS_ACCESS_KEY_ID": "PRIVATE", "DYLD_INSERT_LIBRARIES": "PRIVATE"}):
            report, runner = self.verify(tag="v0.8.0")
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["source_commit"], "a" * 40)
        command = runner.call_args.args[0]
        self.assertNotIn('AWS_ACCESS_KEY_ID', runner.call_args.args[2])
        self.assertNotIn('DYLD_INSERT_LIBRARIES', runner.call_args.args[2])
        for flag, value in (("--repo", tool.UPDATE_REPOSITORY), ("--source-ref", "refs/tags/v0.8.0"),
                            ("--source-digest", "a" * 40), ("--predicate-type", "https://slsa.dev/provenance/v1")):
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn("--deny-self-hosted-runners", command)
        self.assertEqual(command[command.index("--signer-workflow") + 1],
                         tool.UPDATE_REPOSITORY + "/" + tool.PROVENANCE_WORKFLOW + "@refs/tags/v0.8.0")
        self.assertNotIn("--cert-identity", command)
        self.assertIn("--bundle", command)
        self.assertIn("--custom-trusted-root", command)
        self.assertNotIn("PRIVATE", json.dumps(runner.call_args.args[2]))

    def test_wrong_authenticated_certificate_identity_is_rejected(self):
        wrong = {"issuer": "https://evil.invalid", "subjectAlternativeName": "https://github.com/evil/repo/build.yml",
            "sourceRepositoryURI": "https://github.com/evil/repo", "sourceRepositoryIdentifier": "999",
            "sourceRepositoryOwnerIdentifier": "999", "sourceRepositoryOwnerURI": "https://github.com/evil",
            "sourceRepositoryRef": "refs/heads/main", "sourceRepositoryDigest": "b" * 40, "buildSignerDigest": "b" * 40,
            "runnerEnvironment": "self-hosted"}
        for key, value in wrong.items():
            with self.subTest(key=key):
                result = copy.deepcopy(self.verified)
                result[0]["verificationResult"]["signature"]["certificate"][key] = value
                with self.assertRaisesRegex(tool.ProvenanceError, "policy_mismatch"):
                    self.verify(result)

    def test_unprotected_temporary_work_path_is_refused_before_verifier_execution(self):
        original = tool.provenance_safe_path
        def check(path, *args):
            path = Path(path)
            if path.name == tool.MANIFEST and path.parent.name.startswith('dots-ts-provenance-'):
                raise ValueError('untrusted temporary ancestry')
            return original(path, *args)
        with mock.patch.object(tool, 'provenance_safe_path', side_effect=check), \
                mock.patch.object(tool, 'provenance_gh', return_value='/trusted/gh'), \
                mock.patch.object(tool, 'provenance_run', side_effect=AssertionError('unsafe work must not execute verifier')):
            with self.assertRaisesRegex(tool.ProvenanceError, 'provenance_work_directory_unsafe'):
                tool.provenance_verify_payload(self.payload, evidence=self.evidence, provenance_root=self.trusted_root)

    def test_wrong_predicate_source_binding_and_subject_are_rejected(self):
        variants = []
        result = copy.deepcopy(self.verified); result[0]["verificationResult"]["verifiedTimestamps"] = []; variants.append(result)
        for field, value in (("predicateType", "https://evil.invalid"), ("subject", [{"digest": {"sha256": "b" * 64}}])):
            result = copy.deepcopy(self.verified); result[0]["verificationResult"]["statement"][field] = value; variants.append(result)
        for key, value in (("repository", "https://github.com/evil/repo"), ("path", ".github/workflows/other.yml"), ("ref", "refs/tags/v0.9.0")):
            result = copy.deepcopy(self.verified)
            result[0]["verificationResult"]["statement"]["predicate"]["buildDefinition"]["externalParameters"]["workflow"][key] = value
            variants.append(result)
        result = copy.deepcopy(self.verified)
        result[0]["verificationResult"]["statement"]["predicate"]["buildDefinition"]["resolvedDependencies"][0]["digest"]["gitCommit"] = "b" * 40
        variants.append(result)
        for result in variants:
            with self.assertRaisesRegex(tool.ProvenanceError, "policy_mismatch"):
                self.verify(result)

    def test_modified_sources_and_manifest_are_rejected_before_verifier(self):
        self.payload["shim.py"] += b"# modification\n"
        with mock.patch.object(tool, "provenance_gh") as verifier:
            with self.assertRaisesRegex(tool.ProvenanceError, "source_digest_mismatch"):
                tool.provenance_verify_payload(self.payload, evidence=self.evidence, provenance_root=self.trusted_root)
            verifier.assert_not_called()
        self.payload = payload_fixture()
        self.payload[tool.MANIFEST] += b"\n"
        with self.assertRaisesRegex(tool.ProvenanceError, "metadata_invalid"):
            self.verify()

    def test_missing_evidence_wrong_tag_and_unsigned_new_version_fail_closed(self):
        for evidence in (None, {}, {tool.PROVENANCE_METADATA: b"{}"}):
            with self.assertRaisesRegex(tool.ProvenanceError, "evidence_required"):
                tool.provenance_verify_payload(self.payload, evidence=evidence)
        with self.assertRaisesRegex(tool.ProvenanceError, "tag_mismatch"):
            self.verify(tag="v0.9.0")
        with self.assertRaisesRegex(tool.ProvenanceError, "exception_not_applicable"):
            self.verify(allow_unsigned_legacy=True)

    def test_legacy_unsigned_path_requires_explicit_manual_review(self):
        payload = payload_fixture("0.7.1")
        with self.assertRaisesRegex(tool.ProvenanceError, "legacy_operator_review_required"):
            tool.provenance_verify_payload(payload)
        report = tool.provenance_verify_payload(payload, allow_unsigned_legacy=True)
        self.assertEqual(report["status"], "legacy_operator_review")
        self.assertEqual(report["evidence"], "unsigned_operator_review")

    def test_independent_root_and_preinstalled_verifier_required(self):
        with self.assertRaisesRegex(tool.ProvenanceError, "trusted_root_required"):
            tool.provenance_verify_payload(self.payload, evidence=self.evidence)
        with self.assertRaisesRegex(tool.ProvenanceError, "trusted_root_unsafe"):
            tool.provenance_verify_payload(self.payload, evidence=self.evidence, provenance_root=self.trusted_root,
                                           forbidden_directories=(self.root,))
        fake = self.root / "gh"; fake.write_text("not a verifier"); fake.chmod(0o700)
        with mock.patch.object(tool.shutil, "which", return_value=str(fake)):
            with self.assertRaisesRegex(tool.ProvenanceError, "verifier_unsafe"):
                tool.provenance_gh((self.root,))
        self.trusted_root.chmod(0o666)
        with self.assertRaisesRegex(tool.ProvenanceError, "trusted_root_unsafe"):
            tool.provenance_trusted_root(self.trusted_root)

    def test_root_is_read_from_one_descriptor_and_same_inode(self):
        actual_open = os.open
        with mock.patch.object(tool.os, "open", wraps=actual_open) as opened:
            raw = tool.provenance_trusted_root(self.trusted_root)
        self.assertEqual(raw, self.trusted_root.read_bytes())
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(Path(opened.call_args.args[0]), self.trusted_root.resolve())
        self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)
        self.assertTrue(opened.call_args.args[1] & os.O_NONBLOCK)

    def test_actual_opened_root_metadata_is_checked_and_descriptor_closed(self):
        actual_open, actual_fstat = os.open, os.fstat
        for field, value in ((stat.ST_UID, os.getuid() + 10000),
                             (stat.ST_MODE, stat.S_IFREG | 0o666),
                             (stat.ST_MODE, stat.S_IFDIR | 0o700),
                             (stat.ST_INO, self.trusted_root.stat().st_ino + 1)):
            with self.subTest(field=field, value=value):
                descriptors = []
                def opened(*args, **kwargs):
                    descriptor = actual_open(*args, **kwargs); descriptors.append(descriptor)
                    return descriptor
                def changed_metadata(descriptor):
                    values = list(actual_fstat(descriptor)); values[field] = value
                    return os.stat_result(values)
                with mock.patch.object(tool.os, "open", side_effect=opened), \
                        mock.patch.object(tool.os, "fstat", side_effect=changed_metadata):
                    with self.assertRaisesRegex(tool.ProvenanceError, "trusted_root_unsafe"):
                        tool.provenance_trusted_root(self.trusted_root)
                self.assertEqual(len(descriptors), 1)
                with self.assertRaises(OSError):
                    actual_fstat(descriptors[0])

    def test_nonsticky_writable_parent_refuses_root_and_verifier(self):
        shared = self.root / "shared"; shared.mkdir(mode=0o700)
        anchor = shared / "trusted-root.jsonl"; anchor.write_bytes(b"root"); anchor.chmod(0o600)
        verifier = shared / "gh"; verifier.write_bytes(b"fixture"); verifier.chmod(0o700)
        shared.chmod(0o777)
        with self.assertRaisesRegex(tool.ProvenanceError, "trusted_root_unsafe"):
            tool.provenance_trusted_root(anchor)
        with mock.patch.object(tool.shutil, "which", return_value=str(verifier)):
            with self.assertRaisesRegex(tool.ProvenanceError, "verifier_unsafe"):
                tool.provenance_gh()

    def test_trusted_sticky_parent_allows_trusted_child(self):
        shared = self.root / "sticky"; shared.mkdir(mode=0o700)
        anchor = shared / "trusted-root.jsonl"; anchor.write_bytes(b"root"); anchor.chmod(0o600)
        shared.chmod(0o1777)
        self.assertEqual(tool.provenance_trusted_root(anchor), b"root")
        actual_lstat = Path.lstat
        def changed_owner(path):
            value = actual_lstat(path)
            if path == anchor:
                values = list(value); values[stat.ST_UID] = os.getuid() + 10000
                return os.stat_result(values)
            return value
        with mock.patch.object(Path, "lstat", changed_owner):
            with self.assertRaisesRegex(tool.ProvenanceError, "trusted_root_unsafe"):
                tool.provenance_trusted_root(anchor)

    def test_untrusted_ancestor_owner_is_rejected_even_without_write_bits(self):
        folder = self.root / "other-owner"; folder.mkdir(mode=0o700)
        anchor = folder / "root.jsonl"; anchor.write_bytes(b"root"); anchor.chmod(0o600)
        actual_lstat = Path.lstat
        def changed_owner(path):
            value = actual_lstat(path)
            if path == folder:
                values = list(value); values[stat.ST_UID] = os.getuid() + 10000
                return os.stat_result(values)
            return value
        with mock.patch.object(Path, "lstat", changed_owner):
            with self.assertRaisesRegex(tool.ProvenanceError, "trusted_root_unsafe"):
                tool.provenance_trusted_root(anchor)

    def test_unsafe_root_types_and_symlinks_are_refused(self):
        link = self.root / "root-link"; link.symlink_to(self.trusted_root)
        empty = self.root / "empty.jsonl"; empty.touch(mode=0o600)
        folder = self.root / "root-directory"; folder.mkdir(mode=0o700)
        fifo = self.root / "root-fifo"; os.mkfifo(fifo, 0o600)
        for path in (link, empty, folder, fifo):
            with self.subTest(path=path.name), self.assertRaisesRegex(tool.ProvenanceError, "trusted_root_unsafe"):
                tool.provenance_trusted_root(path)

    def test_verifier_symlink_uses_checked_canonical_target(self):
        binary = self.root / "gh-real"; binary.write_bytes(b"fixture"); binary.chmod(0o700)
        link = self.root / "gh"; link.symlink_to(binary)
        with mock.patch.object(tool.shutil, "which", return_value=str(link)):
            self.assertEqual(tool.provenance_gh(), str(binary.resolve()))
            with self.assertRaisesRegex(tool.ProvenanceError, "verifier_unsafe"):
                tool.provenance_gh((self.root,))

    def test_verifier_failures_are_sanitized_and_not_unsigned_fallback(self):
        for response, expected in (((1, b"PRIVATE"), "verification_failed"), ((0, b"not JSON"), "policy_mismatch")):
            with mock.patch.object(tool, "provenance_gh", return_value="/trusted/gh"), \
                    mock.patch.object(tool, "provenance_run", side_effect=[(0, b"gh version 2.103.3"), response]):
                with self.assertRaisesRegex(tool.ProvenanceError, expected):
                    tool.provenance_verify_payload(self.payload, evidence=self.evidence, provenance_root=self.trusted_root)

    def test_public_provenance_errors_use_only_closed_codes(self):
        for raw in ("PRIVATE gh stderr and /private/path", [], {"token": "PRIVATE"}, None):
            error = tool.ProvenanceError(raw)
            self.assertEqual(str(error), "provenance_evidence_invalid")
            self.assertEqual(tool.apply_error_report(error), {"error": "provenance_evidence_invalid"})
        for code in ("provenance_trusted_root_required", "provenance_trusted_root_unsafe",
                     "provenance_verifier_unavailable", "provenance_verifier_unsafe", "provenance_verification_failed"):
            self.assertEqual(tool.apply_error_report(tool.ProvenanceError(code)), {"error": code})

    def test_apply_cli_reports_actionable_sanitized_provenance_reason(self):
        for failure, expected in (("provenance_trusted_root_required", "provenance_trusted_root_required"),
                                  ("provenance_verifier_unavailable", "provenance_verifier_unavailable"),
                                  ("provenance_verification_failed", "provenance_verification_failed"),
                                  ("PRIVATE raw gh stderr", "provenance_evidence_invalid")):
            output = io.StringIO()
            with mock.patch.object(tool, "directories", return_value=(self.root, self.root)), \
                    mock.patch.object(tool, "update_apply", side_effect=tool.ProvenanceError(failure)), \
                    contextlib.redirect_stdout(output):
                code = tool.main(["update", "apply", "--tag", "v0.8.0", "--confirm-recovery-terminal", "--json"])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue()), {"error": expected})
            self.assertNotIn("PRIVATE", output.getvalue())

    def test_old_verifier_is_rejected(self):
        with mock.patch.object(tool, "provenance_gh", return_value="/trusted/gh"), \
                mock.patch.object(tool, "provenance_run", return_value=(0, b"gh version 2.0.0")):
            with self.assertRaisesRegex(tool.ProvenanceError, "version_unsupported"):
                tool.provenance_verify_payload(self.payload, evidence=self.evidence, provenance_root=self.trusted_root)

    def test_verifier_output_and_runtime_are_bounded(self):
        with self.assertRaisesRegex(tool.ProvenanceError, "output_limit"):
            tool.provenance_run([sys.executable, "-c", "print('x'*10000)"], self.root, os.environ.copy(), limit=100)
        with self.assertRaisesRegex(tool.ProvenanceError, "timeout"):
            tool.provenance_run([sys.executable, "-c", "import time;time.sleep(10)"], self.root, os.environ.copy(), timeout=0.1)

    def test_staged_manifest_must_match_verified_receipt_before_candidate_syntax(self):
        with mock.patch.object(tool, "apply_bundle", side_effect=[payload_fixture("0.7.1"), self.payload]), \
                mock.patch.object(tool, "apply_syntax") as syntax:
            with self.assertRaisesRegex(tool.ApplyError, "staged_manifest_mismatch"):
                tool.apply_transaction(self.root, self.root, self.root, {"provenance": {"status": "verified", "tag": "v0.8.0", "manifest_sha256": "b" * 64}}, mock.Mock())
            syntax.assert_not_called()

    def test_apply_requires_proof_before_syntax_replacement_or_worker(self):
        directory = self.root / "install"; directory.mkdir(mode=0o700)
        sentinel = directory / "shim.py"; sentinel.write_bytes(b"original source")
        old = payload_fixture("0.7.1")
        proxy = mock.Mock(); proxy._operation.return_value = contextlib.nullcontext()
        with mock.patch.dict(sys.modules, {"dots_proxy": proxy}), \
                mock.patch.object(tool.sys, "platform", "linux"), \
                mock.patch.object(tool.os, "pidfd_open", create=True), \
                mock.patch.object(signal, "pidfd_send_signal", create=True), \
                mock.patch.object(tool, "apply_bundle", side_effect=[old, old, self.payload]), \
                mock.patch.object(tool, "provenance_bundle_evidence", return_value=self.evidence), \
                mock.patch.object(tool, "provenance_verify_payload", side_effect=tool.ProvenanceError("provenance_verification_failed")), \
                mock.patch.object(tool, "apply_syntax") as syntax, \
                mock.patch.object(tool.subprocess, "Popen") as worker:
            with self.assertRaisesRegex(tool.ApplyError, "provenance_verification_failed"):
                tool.update_apply(directory, self.root, bundle=self.root / "candidate", confirm=True,
                                  provenance_root=self.trusted_root)
            syntax.assert_not_called(); worker.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"original source")


class ReleaseCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="release-candidate-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        spec = importlib.util.spec_from_file_location("release_candidate", ROOT / "scripts/build_release_candidate.py")
        self.builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.builder)
        self.builder.ROOT = self.root
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "scripts").mkdir()
        (self.root / "scripts/source_manifest.py").write_bytes((ROOT / "scripts/source_manifest.py").read_bytes())
        self.payload = payload_fixture()
        for name, raw in self.payload.items():
            (self.root / name).write_bytes(raw)
        (self.root / "VERSION").write_text("0.8.0\n")
        # Match the generator's original canonical manifest bytes.
        subprocess.run([sys.executable, str(self.root / "scripts/source_manifest.py")], check=True)
        subprocess.run(["git", "add", "."], check=True, cwd=self.root)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture"], check=True, cwd=self.root)
        self.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()

    def build(self, tag="v0.8.0", commit=None):
        with mock.patch.object(sys, "argv", ["builder", "--tag", tag, "--commit", commit or self.commit]):
            return self.builder.main()

    def test_exact_tagged_candidate_archives_match_tracked_bytes(self):
        self.build()
        destination = self.root / "dist"
        original = subprocess.check_output(["git", "ls-files", "-z"], cwd=self.root).decode().split("\0")
        expected = {name: (self.root / name).read_bytes() for name in original if name}
        with zipfile.ZipFile(destination / "dots-tailscale-0.8.0.zip") as archive:
            self.assertEqual({name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}, expected)
        with tarfile.open(destination / "dots-tailscale-0.8.0.tar.gz") as archive:
            self.assertEqual({entry.name: archive.extractfile(entry).read() for entry in archive if entry.isfile()}, expected)
        metadata = json.loads((destination / tool.PROVENANCE_METADATA).read_bytes())
        self.assertEqual(metadata["source_commit"], self.commit)
        self.assertEqual(metadata["manifest_sha256"], hashlib.sha256((destination / tool.MANIFEST).read_bytes()).hexdigest())
        for name in tool.CORE_FILES:
            self.assertEqual((destination / name).read_bytes(), expected[name])

    def test_wrong_tag_commit_or_dirty_source_cannot_build(self):
        for tag, commit in (("v0.8.0-rc1", self.commit), ("v0.9.0", self.commit), ("v0.8.0", "b" * 40)):
            with self.assertRaises(SystemExit):
                self.build(tag, commit)
            self.assertFalse((self.root / "dist").exists())
        (self.root / "shim.py").write_text("# dirty source\n")
        with self.assertRaises(SystemExit):
            self.build()
        self.assertFalse((self.root / "dist").exists())

    def test_portable_bundle_workflow_step_completes_all_asset_checksums(self):
        self.build()
        fixture = self.root / "attestation.json"; fixture.write_bytes(b'{"public":"fixture, not a signature"}')
        workflow = (ROOT / ".github/workflows/release-provenance.yml").read_text()
        step = workflow.split("      - name: Retain portable attestation evidence\n", 1)[1]
        body = step.split("        run: |\n", 1)[1].split("      - name: Upload candidate", 1)[0]
        subprocess.run(["bash", "-e", "-c", textwrap.dedent(body)], cwd=self.root,
                       env=dict(os.environ, ATTESTATION_BUNDLE=str(fixture)), check=True)
        destination = self.root / "dist"
        rows = (destination / "SHA256SUMS").read_text().splitlines()
        checksums = {name: digest for digest, name in (line.split("  ", 1) for line in rows)}
        self.assertEqual(len(rows), len(checksums))
        self.assertEqual(set(checksums), {p.name for p in destination.iterdir() if p.name != "SHA256SUMS"})
        self.assertEqual(len(checksums), 9)
        for name, digest in checksums.items():
            self.assertEqual(hashlib.sha256((destination / name).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
