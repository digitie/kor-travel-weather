#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Youn-sok Choi (digitie)
"""동일 tracked archive에서 정상 이미지만 산출한다. 배포·SSH·DB·retag 기능은 없다."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

_spec = importlib.util.spec_from_file_location(
    "weather_normal_contract",
    Path(__file__).with_name("weather-build-contract.py"),
)
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)
ContractError = contract.ContractError
# archive/COPY/cancellation 원형: Transport193 ab69bdc (GPL-3.0-or-later).
PUBLIC_INPUTS = {
    "api": set(),
    "code-server": set(),
    "frontend": {"NEXT_PUBLIC_VWORLD_API_KEY", "NEXT_PUBLIC_DAGSTER_URL"},
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_hash(value) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def run(argv, *, cwd=None, input=None) -> bytes:
    p = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = p.communicate(input, timeout=120)
    except BaseException:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
        raise
    if p.returncode:
        error = ContractError("외부 읽기/검증 명령이 실패했습니다")
        error.stdout = stdout
        error.stderr = stderr
        error.exit_code = p.returncode
        raise error
    return stdout


def git(repo: Path, *args: str) -> str:
    return (
        run(
            [
                "git",
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(repo),
                *args,
            ]
        )
        .decode()
        .strip()
    )


def verify_source(repo: Path, revision: str, tree: str) -> None:
    if any(
        os.environ.get(k)
        for k in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        )
    ):
        raise ContractError("외부 Git context를 거부합니다")
    if Path(git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise ContractError("repo root context가 아닙니다")
    metadata = Path(git(repo, "rev-parse", "--absolute-git-dir"))
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repo / common
    if any(
        (p / "objects/info/alternates").exists() or (p / "info/grafts").exists()
        for p in (metadata, common.resolve())
    ):
        raise ContractError("외부 object/graft를 거부합니다")
    if git(repo, "remote", "get-url", "origin") != contract.BUILD_SOURCE:
        raise ContractError("canonical origin 불일치")
    if git(repo, "rev-parse", "HEAD") != revision or git(repo, "rev-parse", "HEAD^{tree}") != tree:
        raise ContractError("HEAD/tree가 이동했거나 frozen 입력과 다릅니다")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ContractError("dirty source를 거부합니다")


def materialize(repo: Path, revision: str, output: Path) -> dict:
    archive = output / "tracked-source.tar"
    run(
        [
            "git",
            "--no-replace-objects",
            "-c",
            "core.autocrlf=false",
            "-C",
            str(repo),
            "archive",
            "--format=tar",
            "--output",
            str(archive),
            revision,
        ]
    )
    source = output / "source"
    source.mkdir()

    def archive_filter(member, destination):
        safe = tarfile.data_filter(member, destination)
        if safe is not None and member.isdir():
            # data의 경로/소유자 보호는 유지하고 archive의 안전한 directory mode만 복원한다.
            safe = safe.replace(mode=tarfile.tar_filter(member, destination).mode)
        return safe

    with tarfile.open(archive) as t:
        for member in t.getmembers():
            if not member.isfile() and (not member.isdir()):
                raise ContractError("archive link/special file은 지원하지 않습니다")
        t.extractall(source, filter=archive_filter)
    entries, links = ({}, {})
    rows = run(["git", "--no-replace-objects", "-C", str(repo), "ls-tree", "-rz", revision])
    for row in rows.split(b"\x00"):
        if not row:
            continue
        meta, name = row.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = name.decode()
        if kind == "commit" and mode == "160000":
            links[path] = oid
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ContractError("지원하지 않는 tracked file mode")
        p = source / path
        data = p.read_bytes()
        if hashlib.sha1(b"blob " + str(len(data)).encode() + b"\x00" + data).hexdigest() != oid:
            raise ContractError("archive와 Git blob 불일치")
        if bool(p.stat().st_mode & 73) != (mode == "100755"):
            raise ContractError("archive executable mode 불일치")
        entries[path] = {"gitBlob": oid, "sha256": digest(data), "mode": mode}
    snapshot = {
        "files": entries,
        "gitlinks": links,
        "archiveSHA256": digest(archive.read_bytes()),
        "fingerprints": context_fingerprints(source),
    }
    verify_context(source, snapshot)
    return snapshot


def context_fingerprints(source: Path) -> dict:
    result = {}
    for p in [source, *source.rglob("*")]:
        info = p.lstat()
        result[str(p.relative_to(source))] = [
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_gid,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ]
    return result


def verify_context(source: Path, snapshot: dict) -> None:
    if context_fingerprints(source) != snapshot["fingerprints"]:
        raise ContractError("tracked context metadata/bytes 변경")
    actual = {str(p.relative_to(source)): p for p in source.rglob("*") if not p.is_dir()}
    if set(actual) != set(snapshot["files"]):
        raise ContractError("tracked context 파일 목록 변경")
    for name, expected in snapshot["files"].items():
        p = actual[name]
        if p.is_symlink() or digest(p.read_bytes()) != expected["sha256"]:
            raise ContractError("tracked context bytes 변경")
        if bool(p.stat().st_mode & 73) != (expected["mode"] == "100755"):
            raise ContractError("tracked context mode 변경")


def glob_regex(pattern: str) -> str:
    """현재 production dockerignore의 *, **, ?만 지원하고 나머지는 추정하지 않는다."""
    if any(c in pattern for c in "[]\\"):
        raise ContractError("지원하지 않는 dockerignore 패턴")
    result, i = ("", 0)
    while i < len(pattern):
        if pattern[i : i + 3] == "**/":
            result += "(?:.*/)?"
            i += 3
        elif pattern[i : i + 2] == "**":
            result += ".*"
            i += 2
        elif pattern[i] == "*":
            result += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            result += "[^/]"
            i += 1
        else:
            result += re.escape(pattern[i])
            i += 1
    return result


def ignored(path: str, patterns: list[str]) -> bool:
    excluded = False
    ancestors = [path, *[str(p) for p in Path(path).parents if str(p) != "."]]
    for raw in patterns:
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        negative = text.startswith("!")
        text = text.removeprefix("!").strip("/")
        if not text or ".." in Path(text).parts:
            raise ContractError("잘못된 dockerignore 패턴")
        regex = glob_regex(text)
        if any(re.fullmatch(regex, p) for p in ancestors):
            excluded = not negative
    return excluded


def copy_inputs(source: Path, dockerfile: str, snapshot: dict) -> dict:
    ignore = Path(dockerfile + ".dockerignore")
    if not (source / ignore).exists():
        ignore = Path(".dockerignore")
    patterns = (source / ignore).read_text().splitlines() if (source / ignore).exists() else []
    text = (source / dockerfile).read_text().replace("\\\n", " ")
    selected, selectors = ({}, [])
    for line in text.splitlines():
        if not re.match("^\\s*COPY\\s", line, re.IGNORECASE):
            continue
        tokens = shlex.split(line.strip())[1:]
        if any(t.startswith("--from=") for t in tokens):
            continue
        while tokens and tokens[0].startswith("--"):
            if not tokens[0].startswith(("--chmod=", "--chown=", "--link")):
                raise ContractError("지원하지 않는 COPY 옵션")
            tokens.pop(0)
        if len(tokens) < 2:
            raise ContractError("지원하지 않는 COPY 구문")
        for item in tokens[:-1]:
            if (
                any(c in item for c in "$*?[]")
                or Path(item).is_absolute()
                or ".." in Path(item).parts
            ):
                raise ContractError("지원하지 않는 COPY source")
            item = item.removeprefix("./").rstrip("/") or "."
            matches = [
                p
                for p in snapshot["files"]
                if (item == "." or p == item or p.startswith(item + "/"))
                and (not ignored(p, patterns))
            ]
            if not matches:
                raise ContractError("COPY source가 없거나 전부 제외됐습니다")
            selectors.append(item)
            for name in matches:
                selected[name] = snapshot["files"][name]["sha256"]
    if not selected:
        raise ContractError("검증할 COPY 입력이 없습니다")
    return {
        "selectors": selectors,
        "files": selected,
        "sha256": json_hash(selected),
        "dockerignore": str(ignore),
        "dockerignoreSHA256": digest((source / ignore).read_bytes())
        if (source / ignore).exists()
        else None,
    }


def public_inputs(value: dict, roles: list[str]) -> dict:
    allowed = set().union(*(PUBLIC_INPUTS[r] for r in roles))
    if not isinstance(value, dict) or not set(value).issubset(allowed):
        raise ContractError("허용되지 않은 public build input")
    for name, content in value.items():
        if (
            not isinstance(content, str)
            or len(content) > 4096
            or any(c in content for c in "\r\n\x00")
        ):
            raise ContractError("잘못된 public build input")
        if name.endswith("URL") and content:
            try:
                parsed = urlsplit(content)
                _ = parsed.port
            except ValueError as exc:
                raise ContractError("잘못된 public URL") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ContractError("credential/query가 포함된 URL은 public input이 아닙니다")
    return value


def execute_build(command: list[str], env: dict, log: Path, values: list[str]) -> None:
    with log.open("x") as stream:
        p = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            for line in p.stdout:
                for value in sorted({v for v in values if v}, key=len, reverse=True):
                    line = line.replace(value, "[REDACTED]")
                stream.write(line)
                stream.flush()
            code = p.wait()
        except BaseException:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
            raise
        finally:
            p.stdout.close()
        if code:
            raise ContractError("normal Docker build 실패; redacted log를 보세요")


def build(args, inputs: dict) -> dict:
    repo = args.repo.resolve()
    output = args.output_dir.absolute()
    if (
        output.exists()
        or output.is_relative_to(repo)
        or any(p.is_symlink() for p in output.parents)
    ):
        raise ContractError("새 repo 밖 output directory가 필요합니다")
    output = output.resolve()
    if output.is_relative_to(repo):
        raise ContractError("repo 내부 output을 거부합니다")
    roles = args.role
    if any(role not in contract.BUILD_RECIPES for role in roles):
        raise ContractError("지원하지 않는 role")
    if args.output != "docker":
        raise ContractError("loaded native Docker output만 지원합니다")
    native = run(["docker", "info", "--format", "{{.Architecture}}"]).decode().strip()
    if {"aarch64": "arm64", "x86_64": "amd64"}.get(native, native) != args.architecture:
        raise ContractError("native Docker architecture와 다릅니다")
    if len(set(args.role)) != len(args.role):
        raise ContractError("중복 build role")
    inputs = public_inputs(inputs, roles)
    if set(inputs) != set().union(*(PUBLIC_INPUTS[r] for r in roles)) or any(
        not v for v in inputs.values()
    ):
        raise ContractError("선택 role의 모든 public input을 명시해야 합니다")
    verify_source(repo, args.revision, args.expected_tree)
    output.mkdir(parents=True, mode=448)
    snapshot = materialize(repo, args.revision, output)
    source = output / "source"
    write_json(output / "source-manifest.json", snapshot)
    artifacts = {}
    for role in dict.fromkeys(roles):
        verify_source(repo, args.revision, args.expected_tree)
        verify_context(source, snapshot)
        dockerfile, target, build_arguments = contract.BUILD_RECIPES[role]
        if role == "frontend" and target != "runner":
            raise ContractError("web production target은 runner여야 합니다")
        copies = copy_inputs(source, dockerfile, snapshot)
        selected = {k: v for k, v in inputs.items() if k in PUBLIC_INPUTS[role]}
        hashes = {k: digest(v.encode()) for k, v in selected.items()}
        input_hash = json_hash(hashes)
        labels = {
            "org.opencontainers.image.source": contract.BUILD_SOURCE,
            "org.opencontainers.image.revision": args.revision,
            "io.kor-travel-weather.source-tree": args.expected_tree,
            "io.kor-travel-weather.build-role": role,
            "io.kor-travel-weather.copy-sha256": copies["sha256"],
            "io.kor-travel-weather.public-input-sha256": input_hash,
        }
        labels.update(
            {
                "io.kor-travel-weather.public-input-" + name.lower(): value
                for name, value in hashes.items()
            }
        )
        tag = (
            "kor-travel-weather-build:"
            + role
            + "-"
            + args.revision[:12]
            + "-"
            + args.architecture
            + "-"
            + digest(str(output).encode())[:12]
        )
        fixed = {
            name: args.revision if value == "$REVISION" else value
            for name, value in build_arguments.items()
        }
        env = {**os.environ, **selected, **fixed}
        cmd = ["docker", "build"]
        cmd += [
            "--platform",
            "linux/" + args.architecture,
            "--file",
            str(source / dockerfile),
            "--tag",
            tag,
        ]
        if target:
            cmd += ["--target", target]
        for key, value in labels.items():
            cmd += ["--label", key + "=" + value]
        for key in [*sorted(selected), *sorted(fixed)]:
            cmd += ["--build-arg", key]
        iid = output / (role + ".iid")
        cmd += ["--iidfile", str(iid)]
        cmd.append(str(source))
        execute_build(cmd, env, output / (role + ".redacted.log"), list(selected.values()))
        verify_source(repo, args.revision, args.expected_tree)
        verify_context(source, snapshot)
        identity = iid.read_text().strip()
        if not re.fullmatch("sha256:[0-9a-f]{64}", identity):
            raise ContractError("실제 build iid가 없습니다")
        image = json.loads(run(["docker", "image", "inspect", identity]))[0]
        if image["Id"] != identity:
            raise ContractError("반환 iid와 실제 image 불일치")
        contract.validate_image(
            image,
            role,
            args.revision,
            args.architecture,
            args.expected_tree,
            copies["sha256"],
            hashes,
        )
        runtime = contract.audit_runtime(
            identity,
            role,
            source,
            args.revision,
            selected,
            run,
            output / (role + ".runtime-diagnostic.json"),
        )
        write_json(output / (role + ".runtime-audit.json"), runtime)
        artifacts[role] = {
            "imageID": image["Id"],
            "architecture": args.architecture,
            "loaded": args.output == "docker",
            "normalDockerBuild": True,
            "roleProfile": "nonroot-node" if role == "frontend" else "existing-python",
            "runtimeAudit": runtime,
            "dockerfile": dockerfile,
            "COPY": copies,
            "publicInputHashes": hashes,
            "command": cmd,
            "logSHA256": digest((output / (role + ".redacted.log")).read_bytes()),
        }
    receipt = {
        "version": 1,
        "kind": "weather-normal-build",
        "normalDockerBuild": True,
        "mode": "NORMAL_BUILD_ONLY",
        "release_sha": args.revision,
        "tree": args.expected_tree,
        "source": contract.BUILD_SOURCE,
        "architecture": args.architecture,
        "output": args.output,
        "actualApplyAllowed": False,
        "deployReady": False,
        "images": {role: artifacts[role]["imageID"] for role in args.role},
        "sourceManifestSHA256": digest((output / "source-manifest.json").read_bytes()),
        "sourceFiles": len(snapshot["files"]),
        "gitlinks": snapshot["gitlinks"],
        "artifacts": artifacts,
    }
    contract.validate_receipt(receipt)
    write_json(output / "build-receipt.json", receipt)
    return receipt


def write_json(path: Path, value) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "잘못된 build 옵션입니다. --help의 지원 옵션을 확인하세요.\n")


def arguments(argv=None):
    p = QuietParser(description=__doc__, allow_abbrev=False)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--revision", required=True)
    p.add_argument("--expected-tree", required=True)
    p.add_argument(
        "--role",
        choices=sorted(contract.BUILD_RECIPES),
        action="append",
        required=True,
    )
    p.add_argument("--architecture", choices=["amd64", "arm64"], required=True)
    p.add_argument("--output", choices=["docker"], default="docker")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument(
        "--public-inputs",
        type=Path,
        help="허용 public 값의 JSON. -는 stdin이며 값은 argv/log에 출력하지 않는다.",
    )
    a = p.parse_args(argv)
    if not re.fullmatch("[0-9a-f]{40}", a.revision) or not re.fullmatch(
        "[0-9a-f]{40}", a.expected_tree
    ):
        p.error("invalid identity")
    return a


def terminate_owned_build(signum, frame) -> None:
    raise InterruptedError("정상 build 취소 요청")


def main(argv=None) -> int:
    a = arguments(argv)
    os.umask(63)
    signal.signal(signal.SIGTERM, terminate_owned_build)
    try:
        data = {}
        if a.public_inputs:
            text = (
                sys.stdin.read(65537)
                if str(a.public_inputs) == "-"
                else a.public_inputs.read_text()
            )
            if len(text) > 65536:
                raise ContractError("public input JSON 크기 초과")
            data = json.loads(text)
        result = build(a, data)
        print(
            json.dumps(
                {
                    "release_sha": result["release_sha"],
                    "images": result["images"],
                    "receipt": str(a.output_dir / "build-receipt.json"),
                    "actualApplyAllowed": False,
                }
            )
        )
        return 0
    except (
        ContractError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        tarfile.TarError,
    ) as exc:
        print("build-only 거부: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
