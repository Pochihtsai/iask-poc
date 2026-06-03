# iASK 2.0 FAQ Wiki

_集團內部知識庫 — 共 **235** 則 FAQ，跨 **9** 個部門。_

Last update: `2026-05-19`

## 部門

| 部門 | 範疇 | Q 數 |
|---|---|---|
| [CFC](./CFC/_index.md) | 財務（會計、出納、報帳、預算、發票） | 16 |
| [GAC](./GAC/_index.md) | 行政（庶務、採購、會議室、識別證、文具） | 37 |
| [HQ](./HQ/_index.md) | 總部 / 高階管理（PFL 統一作業、ATS、BUBP） | 10 |
| [IPC](./IPC/_index.md) | 採購（採購契約、供應商管理、選商驗收） | 16 |
| [KMC](./KMC/_index.md) | 知識管理（檔案命名、知識分享、AAR、ISO 文管） | 19 |
| [LCC](./LCC/_index.md) | 法務（契約管理、法律文件審核、簽約用印） | 27 |
| [PCC](./PCC/_index.md) | 公司治理（CIS、對外訊息、貴賓接待） | 4 |
| [PMC](./PMC/_index.md) | 專案管理（任務、提案、Pipeline、CEM） | 23 |
| [TMC](./TMC/_index.md) | 人資（薪酬 CB、員工關係 ER、招募、出勤） | 83 |

## 跨部門概念頁（concepts/）

_合成自多部門 FAQ，適合「不知道該找哪個部門」的問題從這裡入口。_

- [採購與供應商管理](./concepts/01-採購與供應商管理.md)
- [任務與專案管理 lifecycle](./concepts/02-任務與專案管理.md)
- [契約與用印](./concepts/03-契約與用印.md)
- [請假與出勤管理](./concepts/04-請假與出勤管理.md)
- [文件管理與溝通規範](./concepts/05-文件管理與溝通規範.md)
- [集團統一作業與審查機制](./concepts/06-集團統一作業與審查機制.md)

## 使用方式

- 直接在 Obsidian 開啟此 vault，或交給 LLM 作為知識庫 context。
- 每個 FAQ 檔附 `[[wikilink]]` 跨頁引用，可在 Obsidian 看 graph。
- 來源：`各單位FAQ/*.xlsx`（部門維護），透過 `scripts/convert_excel_to_vault.py` 自動轉換。
- `concepts/` 是跨部門概念頁，由 LLM 主動合成，可手動微調。
