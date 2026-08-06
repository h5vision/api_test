// snapshot 정보를 볼 수 있는 관리자 페이지 제작
// 개발       vite
// 빌드       tsc --noEmit && vite build
// 미리보기   vite preview
// 구성 방식은 snapshots.ts를 모든 기능이 들어있는 snapshots 독립 모듈로 사용한다
// main.ts는 모듈을 import하고, snapshot 페이지를 sidebar에 추가하는 역할만 한다.
type SnapshotData = Record<string, unknown> | null;

export const SnapshotPage = (snapshotData: SnapshotData = null) => {
  if (!snapshotData) {
    return "Loading...";
  }

  return JSON.stringify(snapshotData);
};

export default SnapshotPage;