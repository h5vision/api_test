# VS Code language registry attribution

`vscode_languages.json` is generated from the `contributes.languages` declarations
shipped with Microsoft Visual Studio Code / Code - OSS. The source project is
[`microsoft/vscode`](https://github.com/microsoft/vscode) and is licensed under the
MIT License.

The generated registry contains language identifiers and matching metadata only.
Run `python tools/sync_vscode_languages.py` to refresh it from an installed VS Code,
or pass `--vscode-app-root` / `--extensions-root` explicitly. Repeat
`--extensions-root` to merge a private or Marketplace extension registry; each
extension's license remains the deployer's responsibility.
