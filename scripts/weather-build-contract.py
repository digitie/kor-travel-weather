# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Youn-sok Choi (digitie)
"""정상 빌드의 이미지/receipt 계약. 운영 선택·migration·apply 기능은 없다."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import tarfile
import uuid
from pathlib import Path

BUILD_SOURCE = "https://github.com/digitie/kor-travel-weather.git"
BUILD_RECIPES = {
    "api": ("deploy/Dockerfile.python", None, {"INSTALL_DAGSTER": "0", "GIT_COMMIT": "$REVISION"}),
    "code-server": (
        "deploy/Dockerfile.python",
        None,
        {"INSTALL_DAGSTER": "1", "GIT_COMMIT": "$REVISION"},
    ),
    "frontend": ("deploy/Dockerfile.web", "runner", {}),
}
PUBLIC_NAMES = {"NEXT_PUBLIC_VWORLD_API_KEY", "NEXT_PUBLIC_DAGSTER_URL"}
COMMON_PACKAGES = {
    "@kor-travel/ui",
    "@kor-travel/tokens",
    "@radix-ui/react-focus-scope",
    "lightningcss",
    "vworld-map-core",
    "vworld-map-web",
}
FRONTEND = Path("packages/kor-travel-weather-admin/frontend")


class ContractError(ValueError):
    """외부 값/secret을 포함하지 않는 계약 오류."""


def validate_image(image, role, revision, architecture, tree, copy_hash, input_hashes):
    if role not in BUILD_RECIPES or architecture not in {"arm64", "amd64"}:
        raise ContractError("지원하지 않는 role/architecture")
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("Id", ""))
        or image.get("Architecture") != architecture
        or image.get("Os") != "linux"
    ):
        raise ContractError("실제 image ID/platform 불일치")
    config = image.get("Config") or {}
    expected = {
        "org.opencontainers.image.source": BUILD_SOURCE,
        "org.opencontainers.image.revision": revision,
        "io.kor-travel-weather.source-tree": tree,
        "io.kor-travel-weather.build-role": role,
        "io.kor-travel-weather.copy-sha256": copy_hash,
        **{"io.kor-travel-weather.public-input-" + k.lower(): v for k, v in input_hashes.items()},
    }
    if any((config.get("Labels") or {}).get(k) != v for k, v in expected.items()):
        raise ContractError("실제 OCI/source/tree/role/COPY/input 불일치")
    if set(input_hashes) != (PUBLIC_NAMES if role == "frontend" else set()):
        raise ContractError("public input hash 역할 계약 불일치")
    if role == "frontend":
        if config.get("User") != "node":
            raise ContractError("frontend 최종 USER node 불일치")
    else:
        revisions = [
            v.split("=", 1)[1]
            for v in config.get("Env", [])
            if v.startswith("KOR_TRAVEL_WEATHER_GIT_COMMIT=")
        ]
        if revisions != [revision]:
            raise ContractError("Python image 기본 revision 불일치")


def validate_receipt(value):
    flags = {
        "version": 1,
        "kind": "weather-normal-build",
        "mode": "NORMAL_BUILD_ONLY",
        "normalDockerBuild": True,
        "output": "docker",
        "actualApplyAllowed": False,
        "deployReady": False,
        "source": BUILD_SOURCE,
    }
    if any(type(value.get(k)) is not type(v) or value[k] != v for k, v in flags.items()):
        raise ContractError("normal build receipt flags 불일치")
    for key in ("release_sha", "tree"):
        if not re.fullmatch("[0-9a-f]{40}", value.get(key, "")):
            raise ContractError("receipt source identity 불일치")
    if value.get("architecture") not in {"arm64", "amd64"}:
        raise ContractError("receipt architecture 불일치")
    images = value.get("images") or {}
    artifacts = value.get("artifacts") or {}
    if not images or set(images) != set(artifacts) or not set(images).issubset(BUILD_RECIPES):
        raise ContractError("receipt role 집합 불일치")
    for role, artifact in artifacts.items():
        if (
            artifact.get("imageID") != images[role]
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", images[role])
            or artifact.get("loaded") is not True
            or artifact.get("normalDockerBuild") is not True
            or artifact.get("architecture") != value["architecture"]
            or not re.fullmatch("[0-9a-f]{64}", artifact.get("COPY", {}).get("sha256", ""))
            or artifact.get("runtimeAudit", {}).get("status") != "PASS"
        ):
            raise ContractError("receipt artifact 증거 불일치")


def common_manifest(source):
    """고정 vendor SRI를 먼저 검증하고 실제 tgz 파일의 SHA256을 만든다."""
    root = source / FRONTEND
    ledger = json.loads((root / "common-artifacts.json").read_text())["packages"]
    expected = {}
    for name in sorted(COMMON_PACKAGES):
        record = ledger[name]
        filename = record["filename"]
        if Path(filename).name != filename:
            raise ContractError("vendor 파일 경계 오류")
        path = root / "vendor" / filename
        raw = path.read_bytes()
        sri = "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode()
        if sri != record["integrity"]:
            raise ContractError("vendor SRI 불일치")
        files = {}
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                if member.isdir():
                    continue
                relative = Path(member.name)
                if (
                    not member.isfile()
                    or not member.name.startswith("package/")
                    or ".." in relative.parts
                    or relative.is_absolute()
                ):
                    raise ContractError("지원하지 않는 vendor member")
                filename = member.name.removeprefix("package/")
                if not filename or filename in files:
                    raise ContractError("중복 vendor member")
                files[filename] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
        expected[name] = files
    return expected


NODE_PROBE = r"""
const fs=require('node:fs'),crypto=require('node:crypto'),cp=require('node:child_process');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const check=(v)=>{if(!v)throw new Error('runtime-contract');};
check(process.getuid()===1000 && process.version==='v22.23.1');
check(cp.execFileSync('npm',['--version'],{encoding:'utf8'}).trim()==='12.0.1');
let count=0;
for(const [pkg,files] of Object.entries(data.common))
 for(const [path,digest] of Object.entries(files)){
  check(hash(fs.readFileSync('/app/node_modules/'+pkg+'/'+path))===digest);count++;
 }
for(const path of ['next.config.ts','middleware.ts','tsconfig.json',
 'public/maplibre/maplibre-gl-worker.mjs','public/maplibre/maplibre-gl-shared.mjs'])
 fs.accessSync('/app/'+path,fs.constants.R_OK);
for(const path of ['/app/.next/cache','/home/node/.npm','/home/node/.cache']){
 const f=path+'/.weather-build-audit-'+data.nonce;
 try{fs.writeFileSync(f,'ok',{flag:'wx'});}finally{if(fs.existsSync(f))fs.unlinkSync(f);}
}
const routes=JSON.parse(fs.readFileSync('/app/.next/server/app-paths-manifest.json','utf8'));
const targets={};
for(const [route,path] of Object.entries(routes))
 targets[route]=hash(fs.readFileSync('/app/.next/server/'+path));
check(Object.keys(targets).length>0);
const presence=Object.fromEntries(Object.keys(data.publicInputs).map(k=>[k,false]));
function scan(root){
 for(const ent of fs.readdirSync(root,{withFileTypes:true})){
  const path=root+'/'+ent.name;
  if(ent.isDirectory())scan(path);
  else if(/\.(js|mjs|html)$/.test(ent.name)){
   const b=fs.readFileSync(path);
   for(const [key,value] of Object.entries(data.publicInputs))
    if(b.includes(Buffer.from(value)))presence[key]=true;
  }
 }
}
scan('/app/.next/static');scan('/app/.next/server/app');
check(Object.values(presence).every(Boolean));
console.log(JSON.stringify({status:'PASS',uid:process.getuid(),node:process.version,npm:'12.0.1',
 commonPackages:Object.keys(data.common).length,commonFiles:count,commonMismatch:0,
 compiledRouteCount:Object.keys(targets).length,compiledTargets:targets,
 compiledPublicInputHashes:Object.fromEntries(Object.entries(data.publicInputs).map(([k,v])=>[k,hash(Buffer.from(v))])),
 configWorkerReadable:true,imageCacheWritable:true,runnerRawUISourceClaim:false}));
"""

PYTHON_PROBE = r"""
import hashlib,importlib,importlib.util,json,os,pathlib,sys
data=json.load(sys.stdin)
sys.path[:0]=['/app/packages/kor-travel-weather-api/src',
              '/app/packages/kor-travel-weather-dagster/src']
assert sys.version_info[:2]==(3,12)
assert os.environ.get('KOR_TRAVEL_WEATHER_GIT_COMMIT')==data['revision']
for name in ['psycopg','pyproj','cryptography.hazmat.bindings._rust']:
 importlib.import_module(name)
cache=[]
for name in ['json','pathlib','urllib.parse']:
 module=importlib.import_module(name)
 source=pathlib.Path(module.__file__)
 pyc=pathlib.Path(importlib.util.cache_from_source(str(source)))
 header=pyc.read_bytes()[:16]
 assert header[:4]==importlib.util.MAGIC_NUMBER and int.from_bytes(header[4:8],'little')==3
 assert header[8:]==importlib.util.source_hash(source.read_bytes())
 cache.append(name)
for path,digest in data['sourceFiles'].items():
 assert hashlib.sha256(pathlib.Path('/app',path).read_bytes()).hexdigest()==digest
if data['role']=='code-server':
 import dagster,kortravelweather_dagster
 role_version=dagster.__version__
else:
 sys.path.insert(0,'/app/packages/kor-travel-weather-api/src')
 assert importlib.util.find_spec('kortravelweather_api') is not None
 role_version='API source located; HTTP acceptance separate'
print(json.dumps({'status':'PASS','python':sys.version.split()[0],'revision':data['revision'],
 'role':data['role'],'nativeImports':3,'checkedHashStdlib':cache,
 'rawSourceFiles':len(data['sourceFiles']),'roleVersion':role_version,'HTTPAcceptance':'NOT_RUN'}))
"""


def audit_runtime(identity, role, source, revision, inputs, run, diagnostic_path):
    """이미지 기본 USER의 network-none probe. 기본 CMD/HTTP 수용은 별도다."""
    nonce = uuid.uuid4().hex
    payload = {"nonce": nonce, "revision": revision, "role": role}
    if role == "frontend":
        payload.update(common=common_manifest(source), publicInputs=inputs)
        command = ["node", "-e", NODE_PROBE]
    else:
        roots = [
            "src",
            "alembic",
            "packages/kor-travel-weather-api/src",
            "packages/kor-travel-weather-dagster/src",
        ]
        payload["sourceFiles"] = {
            str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in roots
            for p in (source / root).rglob("*.py")
        }
        command = ["python", "-I", "-c", PYTHON_PROBE]
    cid = None
    result = None
    audit_name = "weather-build-audit-" + nonce

    def owned(key):
        observed = json.loads(run(["docker", "inspect", key]))[0]
        if (
            not re.fullmatch("[0-9a-f]{64}", observed.get("Id", ""))
            or observed.get("Image") != identity
            or observed.get("Config", {}).get("Labels", {}).get("io.kor-travel-weather.build-audit")
            != nonce
        ):
            raise ContractError("own audit identity 불일치; 다른 container 정리 금지")
        return observed

    try:
        cid = (
            run(
                [
                    "docker",
                    "create",
                    "--interactive",
                    "--network",
                    "none",
                    "--name",
                    audit_name,
                    "--label",
                    "io.kor-travel-weather.build-audit=" + nonce,
                    "--entrypoint",
                    command[0],
                    identity,
                    *command[1:],
                ]
            )
            .decode()
            .strip()
        )
        if not re.fullmatch("[0-9a-f]{64}", cid):
            raise ContractError("own audit container ID 불일치")
        owned(cid)
        raw = run(
            ["docker", "start", "--attach", "--interactive", cid],
            input=json.dumps(payload).encode(),
        )
        result = json.loads(raw)
        state = owned(cid)
        if (
            state["Id"] != cid
            or state["Config"]["Labels"].get("io.kor-travel-weather.build-audit") != nonce
            or state["State"]["Running"]
            or state["State"]["ExitCode"] != 0
            or result.get("status") != "PASS"
        ):
            raise ContractError("runtime probe 불일치")
        return result
    except BaseException as error:
        diagnostic = {
            "status": "FAIL",
            "type": type(error).__name__,
            "containerID": cid,
            "applyAllowed": False,
        }
        for name in ("stdout", "stderr"):
            text = getattr(error, name, b"").decode("utf-8", "replace")
            for value in sorted(inputs.values(), key=len, reverse=True):
                if value:
                    text = text.replace(value, "[REDACTED]")
            diagnostic[name] = text
        if cid and re.fullmatch("[0-9a-f]{64}", cid):
            try:
                observed = owned(cid)["State"]
                diagnostic["state"] = {
                    k: observed.get(k) for k in ("Running", "Status", "ExitCode", "OOMKilled")
                }
            except Exception as inspect_error:
                diagnostic["stateCaptureError"] = type(inspect_error).__name__
        with diagnostic_path.open("x") as stream:
            json.dump(diagnostic, stream, indent=2)
            stream.write("\n")
        raise
    finally:
        # create가 완료됐으나 CLI가 중단되어 CID를 못 받은 경우도 unique name+label로 회수한다.
        try:
            observed = owned(cid if cid and re.fullmatch("[0-9a-f]{64}", cid) else audit_name)
        except Exception:
            observed = None  # identity가 불명확하면 다른 container를 제거하지 않는다.
        cleanup = {
            "name": audit_name,
            "observedID": observed["Id"] if observed else None,
            "identityVerified": observed is not None,
            "removed": False,
        }
        try:
            if observed:
                run(["docker", "rm", "--force", observed["Id"]])
                cleanup["removed"] = True
                if result is not None:
                    result["auditContainerID"] = observed["Id"]
                    result["ownContainerRemoved"] = True
            elif result is not None:
                raise ContractError("audit 정리 identity 미확정")
        finally:
            with diagnostic_path.with_suffix(".cleanup.json").open("x") as stream:
                json.dump(cleanup, stream, indent=2)
                stream.write("\n")
