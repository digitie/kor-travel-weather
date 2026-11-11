// prebuild/predev가 복사한 MapLibre 배포 worker는 소유 소스가 아니다.
// 앱의 모든 JS/TS와 자체 worker 수정은 계속 전수 검사한다.
export default { ignore: { files: ["public/maplibre/**"] } };
