"""실제 Git/archive 검증과 build-only 경계의 순수 회귀검증. Docker 실행은 하지 않는다."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("weather_builder", ROOT / "scripts/build-images.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)
contract = build.contract
REV = "a" * 40
TREE = "b" * 40
IID = "sha256:" + "c" * 64


def image(role="api"):
    hashes = {name: "d" * 64 for name in contract.PUBLIC_NAMES} if role == "frontend" else {}
    value = {
        "Id": IID,
        "Architecture": "arm64",
        "Os": "linux",
        "Config": {
            "Labels": {
                "org.opencontainers.image.source": contract.BUILD_SOURCE,
                "org.opencontainers.image.revision": REV,
                "io.kor-travel-weather.source-tree": TREE,
                "io.kor-travel-weather.build-role": role,
                "io.kor-travel-weather.copy-sha256": "e" * 64,
                **{"io.kor-travel-weather.public-input-" + k.lower(): v for k, v in hashes.items()},
            },
            "User": "node" if role == "frontend" else "",
            "Env": ["KOR_TRAVEL_WEATHER_GIT_COMMIT=" + REV],
        },
    }
    return value, hashes


class GitSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(
            dir="/dev/shm" if Path("/dev/shm").exists() else None
        )
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.g("init", "-q")
        self.g("remote", "add", "origin", contract.BUILD_SOURCE)
        (self.repo / "nested").mkdir()
        (self.repo / "nested/value.txt").write_text("tracked\n")
        (self.repo / "Dockerfile").write_text("FROM scratch\nCOPY nested /app/nested\n")
        self.g("add", ".")
        self.g(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        )
        self.rev = self.g("rev-parse", "HEAD")
        self.tree = self.g("rev-parse", "HEAD^{tree}")

    def g(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args]).decode().strip()

    def test_real_clean_git_source(self):
        build.verify_source(self.repo, self.rev, self.tree)

    def test_dirty_rejected(self):
        (self.repo / "nested/value.txt").write_text("dirty")
        with self.assertRaises(contract.ContractError):
            build.verify_source(self.repo, self.rev, self.tree)

    def test_untracked_rejected(self):
        (self.repo / "untracked").write_text("data")
        with self.assertRaises(contract.ContractError):
            build.verify_source(self.repo, self.rev, self.tree)

    def test_moved_revision_rejected(self):
        with self.assertRaises(contract.ContractError):
            build.verify_source(self.repo, REV, self.tree)

    def test_wrong_origin_rejected(self):
        self.g("remote", "set-url", "origin", "https://example.invalid/other.git")
        with self.assertRaises(contract.ContractError):
            build.verify_source(self.repo, self.rev, self.tree)

    def test_git_environment_rejected(self):
        with (
            patch.dict(os.environ, {"GIT_INDEX_FILE": "/tmp/other-index"}),
            self.assertRaises(contract.ContractError),
        ):
            build.verify_source(self.repo, self.rev, self.tree)

    def test_common_git_alternates_rejected(self):
        path = self.repo / ".git/objects/info/alternates"
        path.write_text(str(self.repo / ".git/objects") + "\n")
        with self.assertRaises(contract.ContractError):
            build.verify_source(self.repo, self.rev, self.tree)

    def test_archive_under_restrictive_umask_retains_traversal(self):
        output = self.root / "output"
        output.mkdir(mode=0o700)
        previous = os.umask(0o077)
        try:
            snapshot = build.materialize(self.repo, self.rev, output)
        finally:
            os.umask(previous)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertTrue((output / "source/nested").stat().st_mode & 0o005 == 0o005)
        self.assertEqual((output / "source/nested/value.txt").read_text(), "tracked\n")
        self.assertEqual(len(snapshot["files"]), 2)

    def test_context_bytes_changed_rejected(self):
        output = self.root / "out"
        output.mkdir()
        snapshot = build.materialize(self.repo, self.rev, output)
        (output / "source/nested/value.txt").write_text("changed")
        with self.assertRaises(contract.ContractError):
            build.verify_context(output / "source", snapshot)

    def test_copy_manifest_tracks_real_inputs(self):
        output = self.root / "out"
        output.mkdir()
        snapshot = build.materialize(self.repo, self.rev, output)
        copies = build.copy_inputs(output / "source", "Dockerfile", snapshot)
        self.assertEqual(set(copies["files"]), {"nested/value.txt"})
        self.assertEqual(copies["files"]["nested/value.txt"], build.digest(b"tracked\n"))

    def test_symlink_archive_rejected(self):
        (self.repo / "link").symlink_to("nested/value.txt")
        self.g("add", "link")
        self.g(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "link",
        )
        output = self.root / "out"
        output.mkdir()
        with self.assertRaises(contract.ContractError):
            build.materialize(self.repo, self.g("rev-parse", "HEAD"), output)

    def test_output_in_source_rejected_before_docker(self):
        args = Namespace(repo=self.repo, output_dir=self.repo / "out", role=["api"])
        with patch.object(build, "run") as docker:
            with self.assertRaises(contract.ContractError):
                build.build(args, {})
            docker.assert_not_called()


class MockBuildOrchestrationTests(unittest.TestCase):
    """실제 Git/archive, MOCK Docker 경계. 실제 image/runtime PASS 증거가 아니다."""

    setUp = GitSourceTests.setUp
    g = GitSourceTests.g

    def test_three_roles_bind_argv_env_labels_receipt(self):
        (self.repo / "deploy").mkdir()
        for name in ("Dockerfile.python", "Dockerfile.web"):
            (self.repo / "deploy" / name).write_text(
                "FROM scratch AS runner\nCOPY nested /app/nested\n"
            )
        self.g("add", "deploy")
        self.g(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Docker fixtures",
        )
        revision = self.g("rev-parse", "HEAD")
        tree = self.g("rev-parse", "HEAD^{tree}")
        output = self.root / "normal-out"
        roles = ["api", "code-server", "frontend"]
        args = Namespace(
            repo=self.repo,
            output_dir=output,
            role=roles,
            revision=revision,
            expected_tree=tree,
            architecture="arm64",
            output="docker",
        )
        inputs = {
            "NEXT_PUBLIC_VWORLD_API_KEY": "fixture-only",
            "NEXT_PUBLIC_DAGSTER_URL": "https://fixture.invalid",
        }
        produced = {}
        original_run = build.run

        def mock_execute(argv, env, log, values):
            labels = dict(argv[i + 1].split("=", 1) for i, x in enumerate(argv) if x == "--label")
            role = labels["io.kor-travel-weather.build-role"]
            image_id = "sha256:" + build.digest(role.encode())
            if role == "frontend":
                self.assertEqual(argv[argv.index("--target") + 1], "runner")
                self.assertEqual(env["NEXT_PUBLIC_VWORLD_API_KEY"], "fixture-only")
                self.assertNotIn("fixture-only", " ".join(argv))
            else:
                self.assertEqual(env["GIT_COMMIT"], revision)
                self.assertEqual(env["INSTALL_DAGSTER"], "0" if role == "api" else "1")
            self.assertEqual(argv[:2], ["docker", "build"])
            self.assertNotIn("tag", argv)
            produced[image_id] = {
                "Id": image_id,
                "Architecture": "arm64",
                "Os": "linux",
                "Config": {
                    "Labels": labels,
                    "User": "node" if role == "frontend" else "",
                    "Env": ["KOR_TRAVEL_WEATHER_GIT_COMMIT=" + revision],
                },
            }
            Path(argv[argv.index("--iidfile") + 1]).write_text(image_id + "\n")
            log.write_text("MOCK Docker orchestration only\n")

        def mock_run(argv, **kwargs):
            if argv[:2] == ["docker", "info"]:
                return b"aarch64\n"
            if argv[:3] == ["docker", "image", "inspect"]:
                return json.dumps([produced[argv[3]]]).encode()
            return original_run(argv, **kwargs)

        with (
            patch.object(build, "run", side_effect=mock_run),
            patch.object(build, "execute_build", side_effect=mock_execute),
            patch.object(
                contract,
                "audit_runtime",
                return_value={"status": "PASS", "ownContainerRemoved": True, "MOCK_ONLY": True},
            ),
        ):
            receipt = build.build(args, inputs)
        self.assertEqual(set(receipt["images"]), set(roles))
        self.assertEqual(len(set(receipt["images"].values())), 3)
        self.assertFalse(receipt["actualApplyAllowed"])
        self.assertFalse(receipt["deployReady"])
        self.assertEqual(receipt["release_sha"], revision)
        self.assertEqual(receipt["tree"], tree)
        for artifact in receipt["artifacts"].values():
            self.assertEqual(set(artifact["COPY"]["files"]), {"nested/value.txt"})
            self.assertTrue(artifact["runtimeAudit"]["MOCK_ONLY"])


class ImageContractTests(unittest.TestCase):
    def verify(self, value, role="api", hashes=None):
        contract.validate_image(value, role, REV, "arm64", TREE, "e" * 64, hashes or {})

    def test_api_baked_revision_and_python_profile(self):
        self.verify(image()[0])

    def test_code_server_is_distinct_build_role(self):
        value, _ = image("code-server")
        self.verify(value, "code-server")
        self.assertNotEqual(
            contract.BUILD_RECIPES["api"][2], contract.BUILD_RECIPES["code-server"][2]
        )

    def test_wrong_revision_source_tree_role_and_copy_fail(self):
        for key in (
            "org.opencontainers.image.revision",
            "org.opencontainers.image.source",
            "io.kor-travel-weather.source-tree",
            "io.kor-travel-weather.build-role",
            "io.kor-travel-weather.copy-sha256",
        ):
            value, _ = image()
            value["Config"]["Labels"][key] = "wrong"
            with self.subTest(key=key), self.assertRaises(contract.ContractError):
                self.verify(value)

    def test_wrong_architecture_or_id_rejected(self):
        for key, wrong in (("Architecture", "amd64"), ("Id", "tag:mutable"), ("Os", "windows")):
            value, _ = image()
            value[key] = wrong
            with self.subTest(key=key), self.assertRaises(contract.ContractError):
                self.verify(value)

    def test_python_missing_duplicate_wrong_baked_revision_rejected(self):
        for values in (
            [],
            ["KOR_TRAVEL_WEATHER_GIT_COMMIT=wrong"],
            ["KOR_TRAVEL_WEATHER_GIT_COMMIT=" + REV] * 2,
        ):
            value, _ = image()
            value["Config"]["Env"] = values
            with self.subTest(values=values), self.assertRaises(contract.ContractError):
                self.verify(value)

    def test_frontend_no_invented_default_revision(self):
        value, hashes = image("frontend")
        value["Config"]["Env"] = ["NODE_ENV=production"]
        self.verify(value, "frontend", hashes)

    def test_frontend_root_rejected(self):
        value, hashes = image("frontend")
        value["Config"]["User"] = "root"
        with self.assertRaises(contract.ContractError):
            self.verify(value, "frontend", hashes)

    def test_frontend_input_hash_mismatch_rejected(self):
        value, hashes = image("frontend")
        hashes = dict.fromkeys(hashes, "f" * 64)
        with self.assertRaises(contract.ContractError):
            self.verify(value, "frontend", hashes)


class ProcessAndInputTests(unittest.TestCase):
    def test_no_private_or_unknown_build_inputs(self):
        for value in ({"DATABASE_URL": "private"}, {"NEXT_PUBLIC_VWORLD_API_KEY": "line\nvalue"}):
            with self.assertRaises(contract.ContractError):
                build.public_inputs(value, ["frontend"])

    def test_url_userinfo_query_fragment_rejected(self):
        for value in (
            "https://user:pass@example.invalid",
            "https://example.invalid?key=value",
            "https://example.invalid/#value",
        ):
            with self.subTest(value=value), self.assertRaises(contract.ContractError):
                build.public_inputs({"NEXT_PUBLIC_DAGSTER_URL": value}, ["frontend"])

    def test_redacted_stream_and_normal_exit_never_kill(self):
        child = Mock()
        child.stdout = io.StringIO("prefix public-value suffix\n")
        child.wait.return_value = 0
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "build.log"
            with (
                patch.object(build.subprocess, "Popen", return_value=child),
                patch.object(build.os, "killpg") as kill,
            ):
                build.execute_build(["docker", "build"], {}, log, ["public-value"])
            self.assertEqual(log.read_text(), "prefix [REDACTED] suffix\n")
            kill.assert_not_called()

    def test_eof_final_wait_interruption_reaps_owned_group(self):
        child = Mock()
        child.pid = 123456
        child.stdout = io.StringIO("")
        child.poll.return_value = None
        child.wait.side_effect = [InterruptedError("cancel"), 0]
        with tempfile.TemporaryDirectory() as d:
            with (
                patch.object(build.subprocess, "Popen", return_value=child),
                patch.object(build.os, "killpg") as kill,
                self.assertRaises(InterruptedError),
            ):
                build.execute_build(["docker", "build"], {}, Path(d) / "log", [])
            kill.assert_called_once_with(child.pid, build.signal.SIGTERM)
            self.assertEqual(child.wait.call_count, 2)
            child.stdout.close()

    def test_term_timeout_escalates_and_reaps(self):
        child = Mock()
        child.pid = 123456
        child.stdout = io.StringIO("")
        child.poll.return_value = None
        child.wait.side_effect = [InterruptedError(), subprocess.TimeoutExpired("owned", 15), 0]
        with tempfile.TemporaryDirectory() as d:
            with (
                patch.object(build.subprocess, "Popen", return_value=child),
                patch.object(build.os, "killpg") as kill,
                self.assertRaises(InterruptedError),
            ):
                build.execute_build(["docker", "build"], {}, Path(d) / "log", [])
            self.assertEqual(
                [x.args[1] for x in kill.call_args_list],
                [build.signal.SIGTERM, build.signal.SIGKILL],
            )

    def test_runtime_failure_preserved_before_own_cleanup(self):
        cid = "1" * 64
        error = contract.ContractError("probe failed")
        error.stdout = b""
        error.stderr = b"public-value native module missing"
        calls = []
        nonce = None

        def run(command, **kwargs):
            nonlocal nonce
            calls.append(command)
            if command[1] == "create":
                nonce = command[command.index("--label") + 1].split("=", 1)[1]
                return cid.encode()
            if command[1] == "start":
                raise error
            if command[1] == "inspect":
                return json.dumps(
                    [
                        {
                            "Id": cid,
                            "Image": IID,
                            "Config": {"Labels": {"io.kor-travel-weather.build-audit": nonce}},
                            "State": {"Status": "exited", "ExitCode": 1},
                        }
                    ]
                ).encode()
            if command[1] == "rm":
                self.assertTrue(diag.exists())
                return b""
            raise AssertionError("unexpected call")

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            diag = root / "diagnostic.json"
            with self.assertRaises(contract.ContractError):
                contract.audit_runtime(
                    IID, "api", root, REV, {"NEXT_PUBLIC_VWORLD_API_KEY": "public-value"}, run, diag
                )
            observed = json.loads(diag.read_text())
            self.assertNotIn("public-value", observed["stderr"])
            self.assertEqual(observed["state"]["ExitCode"], 1)
            self.assertEqual(calls[-1], ["docker", "rm", "--force", cid])


class AuditOwnershipTests(unittest.TestCase):
    def test_wrong_image_cid_never_started_or_removed(self):
        calls = []
        nonce = None
        cid = "2" * 64

        def run(command, **kwargs):
            nonlocal nonce
            calls.append(command)
            if command[1] == "create":
                nonce = command[command.index("--label") + 1].split("=", 1)[1]
                return cid.encode()
            if command[1] == "inspect":
                return json.dumps(
                    [
                        {
                            "Id": cid,
                            "Image": "sha256:" + "f" * 64,
                            "Config": {"Labels": {"io.kor-travel-weather.build-audit": nonce}},
                        }
                    ]
                ).encode()
            raise AssertionError("foreign container action forbidden")

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(contract.ContractError):
                contract.audit_runtime(IID, "api", root, REV, {}, run, root / "fail.json")
            self.assertFalse(any(x[1] in ("start", "rm") for x in calls))

    def test_interrupted_create_recovers_only_exact_nonce_name(self):
        calls = []
        nonce = None
        cid = "3" * 64

        def run(command, **kwargs):
            nonlocal nonce
            calls.append(command)
            if command[1] == "create":
                nonce = command[command.index("--label") + 1].split("=", 1)[1]
                raise InterruptedError("after create before CID stdout")
            if command[1] == "inspect":
                self.assertEqual(command[2], "weather-build-audit-" + nonce)
                return json.dumps(
                    [
                        {
                            "Id": cid,
                            "Image": IID,
                            "Config": {"Labels": {"io.kor-travel-weather.build-audit": nonce}},
                        }
                    ]
                ).encode()
            if command[1] == "rm":
                self.assertEqual(command[3], cid)
                return b""
            raise AssertionError("unexpected action")

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(InterruptedError):
                contract.audit_runtime(IID, "api", root, REV, {}, run, root / "fail.json")
            cleanup = json.loads((root / "fail.cleanup.json").read_text())
            self.assertTrue(cleanup["removed"])
            self.assertTrue(cleanup["identityVerified"])


class ReceiptTests(unittest.TestCase):
    def receipt(self):
        return {
            "version": 1,
            "kind": "weather-normal-build",
            "mode": "NORMAL_BUILD_ONLY",
            "normalDockerBuild": True,
            "output": "docker",
            "source": contract.BUILD_SOURCE,
            "actualApplyAllowed": False,
            "deployReady": False,
            "architecture": "arm64",
            "release_sha": REV,
            "tree": TREE,
            "images": {"api": IID},
            "artifacts": {
                "api": {
                    "imageID": IID,
                    "loaded": True,
                    "normalDockerBuild": True,
                    "architecture": "arm64",
                    "COPY": {"sha256": "e" * 64},
                    "runtimeAudit": {"status": "PASS"},
                }
            },
        }

    def test_build_only_receipt(self):
        contract.validate_receipt(self.receipt())

    def test_deploy_adoption_or_truthy_flags_rejected(self):
        for key, value in (
            ("deployReady", True),
            ("actualApplyAllowed", True),
            ("normalDockerBuild", 1),
            ("version", True),
            ("output", "oci"),
        ):
            receipt = self.receipt()
            receipt[key] = value
            with self.subTest(key=key), self.assertRaises(contract.ContractError):
                contract.validate_receipt(receipt)

    def test_unloaded_failed_or_mismatched_artifacts_rejected(self):
        for key, value in (
            ("loaded", False),
            ("imageID", "sha256:" + "f" * 64),
            ("architecture", "amd64"),
            ("runtimeAudit", {"status": "NOT_RUN"}),
        ):
            receipt = self.receipt()
            receipt["artifacts"]["api"][key] = value
            with self.subTest(key=key), self.assertRaises(contract.ContractError):
                contract.validate_receipt(receipt)

    def test_role_mapping_cannot_be_invented(self):
        receipt = self.receipt()
        receipt["images"]["codeServer"] = receipt["images"].pop("api")
        receipt["artifacts"]["codeServer"] = receipt["artifacts"].pop("api")
        with self.assertRaises(contract.ContractError):
            contract.validate_receipt(receipt)

    def test_partial_role_receipt_not_fullstack_claim(self):
        receipt = self.receipt()
        contract.validate_receipt(receipt)
        self.assertEqual(set(receipt["images"]), {"api"})
        self.assertFalse(receipt["actualApplyAllowed"])


class NativeBoundaryTests(unittest.TestCase):
    def test_foreign_architecture_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "out"
            args = Namespace(
                repo=Path(d), output_dir=output, role=["api"], output="docker", architecture="arm64"
            )
            with (
                patch.object(build, "run", return_value=b"x86_64\n"),
                self.assertRaises(contract.ContractError),
            ):
                build.build(args, {})
            self.assertFalse(output.exists())

    def test_oci_not_an_accidental_export_path(self):
        with tempfile.TemporaryDirectory() as d:
            args = Namespace(
                repo=Path(d),
                output_dir=Path(d).parent / "uncreated-weather-test",
                role=["api"],
                output="oci",
            )
            with patch.object(build, "run") as docker:
                with self.assertRaises(contract.ContractError):
                    build.build(args, {})
                docker.assert_not_called()


class RealVendorTests(unittest.TestCase):
    def test_actual_c11_vendor_sri_and_file_inventory(self):
        files = contract.common_manifest(ROOT)
        self.assertEqual(set(files), contract.COMMON_PACKAGES)
        self.assertEqual(sum(len(x) for x in files.values()), 263)


if __name__ == "__main__":
    unittest.main()
