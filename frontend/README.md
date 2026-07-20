# HancomAI5 VS Code AI Assistant Extension

이 폴더는 상위 디렉터리의 FastAPI 서버와 통신하는 VS Code 확장 프로그램입니다.

```powershell
npm install
npm run compile
```

프로젝트 루트를 VS Code로 열고 `F5`를 누르면 Extension Development Host가 실행됩니다. 명령 팔레트에서 다음 명령을 사용할 수 있습니다.

- `Hancom AI: 채팅 열기`
- `Hancom AI: 현재 파일 인덱싱`
- `Hancom AI: 백엔드 연결 확인`

기본 백엔드 URL은 `http://127.0.0.1:8000`이며 VS Code 설정의 `hancomAi.backendUrl`에서 변경할 수 있습니다.

