# 정규화(Normalization) 모듈 분리 — 분석 및 마이그레이션 가이드

## 결론

**가능하며, 안전합니다.** 조사한 정규화 함수들은 모두 순수 함수(pure function)로,
PostgreSQL, FastAPI Request/Response, 파일시스템 등 외부 상태에 의존하지 않습니다.
따라서 `backend/normalization.py` 하나로 추출해도 기존 동작을 깨지 않고,
오히려 단위 테스트가 훨씬 쉬워집니다.

## 왜 필요한가

현재 정규화 로직이 최소 8개 파일에 중복/산재되어 있습니다.

| 파일 | 중복된 로직 |
|---|---|
| `snapshot_compare.py` | `normalize_branch`, `normalize_project_id`, `normalize_optional_identity`, `normalize_commit_id` |
| `snapshots/contracts.py` | `normalize_git_sha`, `normalize_repository_path`, `normalize_repository_full_name` |
| `language_registry.py` | `_normalized_path` (private, 재사용 불가) |
| `project_resolution.py` | `_reference_key` (private, 재사용 불가) |
| `local_projects.py` | `project_id.strip()` 인라인 |
| `model_access.py` | `model_id.strip()` 인라인 |
| `distributed.py` | `name.strip().replace(" ", "_")` 인라인 |
| `admin_snapshots.py` | `_repository_full_name_from_url`, `normalize_repository_url` |

같은 "strip + None 처리" 패턴이 최소 6곳에서 각자 재구현되어 있고,
`_reference_key`, `_normalized_path` 처럼 private(`_` 접두사)로 선언되어
다른 도메인에서 재사용하고 싶어도 import할 수 없는 것들도 있습니다.

## 새 모듈: `backend/normalization.py`

이미 작성해서 Drive에 올려두었습니다. 제공 함수:

| 함수 | 대체하는 기존 로직 |
|---|---|
| `normalize_optional_text()` | `snapshot_compare.normalize_optional_identity` |
| `normalize_identifier()` | `local_projects`/`model_access`의 인라인 `.strip()` 가드 |
| `normalize_path_like_identifier()` | `snapshot_compare.normalize_project_id` |
| `normalize_lock_key()` | `distributed.py`의 인라인 락 키 정규화 |
| `normalize_commit_sha_case()` | `snapshot_compare.normalize_commit_id` |
| `normalize_git_sha()` | `snapshots/contracts.normalize_git_sha` |
| `normalize_repository_path()` | `snapshots/contracts.normalize_repository_path` |
| `normalize_repository_full_name()` | `admin_snapshots._repository_full_name_from_url` 내부 로직 |
| `normalize_path_separators()` | `language_registry._normalized_path`, `text.classify_index_path` 상단부 |
| `reference_key()` | `project_resolution._reference_key` |
| `slug_key()` | (신규 — 여러 곳에서 필요한데 지금은 각자 다르게 처리 중) |

## 마이그레이션 방법 — 왜 제가 기존 파일을 직접 못 고치는지

이전에 설명드렸듯, 지금 연결된 Google Drive 도구는 **새 파일 생성(`create_file`)**과
**읽기**만 가능하고, **기존 파일을 그 자리에서 덮어쓰는 update 기능이 없습니다.**
그래서 새 모듈은 만들어서 올렸지만, 기존 8개 파일을 열어서 import 문을 바꾸는 작업은
제가 지금 이 자리에서 직접 반영할 수 없습니다.

대신 각 파일에 적용할 정확한 변경사항(diff)을 아래에 정리해두었습니다.
Claude Code나 직접 편집으로 적용하시면 됩니다.

### 1. `snapshot_compare.py`

```diff
+ from .normalization import (
+     normalize_commit_sha_case,
+     normalize_optional_text,
+     normalize_path_like_identifier,
+ )

  @field_validator("project_id")
  @classmethod
  def normalize_project_id(cls, value: str) -> str:
-     normalized = value.strip().strip("/")
-     if not normalized:
-         raise ValueError("project_id must not be blank")
-     return normalized
+     return normalize_path_like_identifier(value, field_name="project_id")

  @field_validator("commit_id", "snapshot_id", mode="before")
  @classmethod
  def normalize_optional_identity(cls, value: Any) -> Any:
-     if value is None:
-         return None
-     if isinstance(value, str):
-         normalized = value.strip()
-         if normalized.casefold() in {"", "none", "null", "undefined"}:
-             return None
-         return normalized
-     return value
+     return normalize_optional_text(value)

  @field_validator("commit_id")
  @classmethod
  def normalize_commit_id(cls, value: str | None) -> str | None:
-     return value.lower() if value else None
+     return normalize_commit_sha_case(value)
```

### 2. `snapshots/contracts.py`

```diff
+ from ..normalization import (
+     normalize_git_sha as _normalize_git_sha,
+     normalize_repository_path as _normalize_repository_path,
+     normalize_repository_full_name as _normalize_repository_full_name,
+ )

  def normalize_git_sha(value: str) -> str:
-     ...(기존 구현)...
+     return _normalize_git_sha(value)

  def normalize_repository_path(value: str) -> str:
-     ...(기존 구현)...
+     return _normalize_repository_path(value)
```

> 기존 공개 함수명(`normalize_git_sha` 등)은 다른 모듈에서 이미 이 경로로
> import하고 있으므로 그대로 유지하고, 내부에서 새 모듈에 위임하는 형태로
> 바꾸는 게 가장 안전합니다 (하위 호환 유지).

### 3. `language_registry.py`

```diff
+ from .normalization import normalize_path_separators

- def _normalized_path(value: str | None) -> str:
-     if not value:
-         return ""
-     candidate = value.strip()
-     if candidate.startswith("file:"):
-         parsed = urlparse(candidate)
-         candidate = unquote(parsed.path)
-     return candidate.replace("\\", "/")
+ def _normalized_path(value: str | None) -> str:
+     if not value:
+         return ""
+     candidate = value.strip()
+     if candidate.startswith("file:"):
+         parsed = urlparse(candidate)
+         candidate = unquote(parsed.path)
+     return normalize_path_separators(candidate)
```

> `file:` URI 파싱은 이 파일만의 특수 로직이라 공유 모듈로 옮기지 않고,
> 마지막 슬래시 정규화 단계만 공유 함수로 위임했습니다.

### 4. `project_resolution.py`

```diff
+ from .normalization import reference_key as _reference_key
- def _reference_key(value: str) -> str:
-     return value.strip().casefold()
```

기존 private 함수를 지우고 공유 모듈에서 import — 이후 다른 도메인에서도
`from .normalization import reference_key`로 동일한 비교 의미론을 재사용 가능.

### 5. `local_projects.py`, `model_access.py`, `distributed.py`

각 파일의 인라인 `.strip()` 가드를 `normalize_identifier()` /
`normalize_lock_key()` 호출로 교체 (패턴은 위 diff들과 동일).

### 6. `admin_snapshots.py`

```diff
+ from .normalization import normalize_repository_full_name

  def _repository_full_name_from_url(value: str) -> str:
      raw = value.strip()
      if not raw or "\x00" in raw:
          raise ValueError("GitHub repository address must not be blank")
      if "://" not in raw:
          lowered = raw.casefold()
          if lowered.startswith("github.com/") or lowered.startswith("www.github.com/"):
              raw = "https://" + raw
          else:
-             candidate = raw.strip("/")
-             if candidate.casefold().endswith(".git"):
-                 candidate = candidate[:-4]
-             return normalize_repository_full_name(candidate)
+             return normalize_repository_full_name(raw)
      ...
```

## 테스트 전략

새 모듈은 DB/FastAPI 의존이 전혀 없으므로, `verify_*.py` 검증 스크립트 패턴을 따라
`verify_normalization.py`를 하나 추가해서 각 함수를 독립적으로 검증할 수 있습니다.
(예: `normalize_git_sha`의 39/41/63/65자 거부 케이스, `normalize_repository_path`의
`../`, 절대경로, 인코딩된 traversal 거부 케이스 등 — 이미 `verify_github_snapshot_mvp.py`에
있는 검증 케이스를 그대로 재사용 가능합니다.)

## 위험도 평가

- **낮음.** 순수 함수 추출이라 동작 변경이 없습니다 (로직 100% 동일하게 이식).
- 유일한 주의점: `snapshots/contracts.py`의 `normalize_git_sha` 등은 이미 여러 곳에서
  `from .snapshots.contracts import normalize_git_sha` 형태로 import되고 있으므로,
  **공개 함수 이름과 위치는 그대로 유지**하고 내부 구현만 새 모듈에 위임해야
  다른 파일들을 추가로 건드릴 필요가 없습니다.
