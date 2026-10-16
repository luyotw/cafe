# QA Checklist

- [x] 完整載入 role、develop、plan、workspace；核對精確 HEAD／changed-file set／來源 hash。
- [x] 依 Requirements Specification 及第一階段適用表執行 U-REQUIRED／NEXT／SETTINGS／I18N／SCHEMA-COMPAT／DAY-SOURCE／DAY-ORDER／LEGACY-MAPPING／INTENT-SCOPE 的可觀察檢查。
- [x] 獨立執行真 SDK／Rules 的 J-START／FAMILY／SETTINGS／APPEND／DAY-ORDER／RETRY 及 snapshot／view 同交易適用子項。
- [x] 獨立執行真 Chrome／IndexedDB 恢復與 UI 保存、四孩、鍵盤、文字放大、語言、reduced motion 情境。
- [x] 記錄每項 PASS／FAIL／未驗、方法與版本；區分 fixture、emulator、headless 及正式 consumer／實機延期責任。
- [x] 不修改產品程式；核對全部發現與 Todo evidence，保存重現細節、流程終態與 exit_code。
- [x] 寫完整 QA report、重驗適用 checklist，依結果寫有效 baton；保留 PLAN-008／011、#67 OPEN 及來源。
