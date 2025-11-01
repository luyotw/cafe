## 實作分析

* 程式碼風格參考範例
    * 服務層模式：`services/user_service.py`
    * View 層風格：`views/user_views.py`
    * Model 使用：`User.objects.filter()` 的使用方式
    * 例外處理：使用自定義 Exception 類別
* 實作細節
    * 建立 `services/auth_service.py`，包含 `AuthService` 類別
    * 實作 `authenticate()` 和 `register_user()` 方法
    * 搬移內容包括：
        * 密碼驗證邏輯
        * JWT token 產生邏輯
        * 使用者註冊驗證（email 格式、密碼強度等）
        * 資料庫查詢和建立使用者
    * 服務層回傳 `ServiceResult` 物件（使用 dataclass）
    * View 層保留：
        * HTTP 請求參數取得和基本驗證
        * 呼叫服務層方法
        * 根據服務層結果回傳 HTTP 回應（200/400/401/500）
    * 不需要資料庫遷移、不需要修改 API 格式
* 資料格式範例
    ```python
    # 服務層回傳的物件結構
    @dataclass
    class ServiceResult:
        success: bool
        data: Optional[Dict[str, Any]] = None
        error: Optional[str] = None
        error_code: Optional[str] = None

    # 登入成功範例
    ServiceResult(
        success=True,
        data={
            'user_id': 123,
            'username': 'john_doe',
            'token': 'eyJhbGciOiJIUzI1NiIs...',
            'expires_at': 1728012345
        }
    )

    # 登入失敗範例
    ServiceResult(
        success=False,
        error='Invalid credentials',
        error_code='AUTH_INVALID_CREDENTIALS'
    )

    # View 層的 JSON 回應（維持現有格式）
    {
        "success": true,
        "data": {
            "user_id": 123,
            "username": "john_doe",
            "token": "eyJhbGciOiJIUzI1NiIs...",
            "expires_at": 1728012345
        }
    }
    ```

### 📋 開發任務拆解

#### Task 1: 建立服務層基礎結構
- [ ] **開發 1.1**：建立 `services/auth_service.py`，定義 `AuthService` 類別
- [ ] **開發 1.2**：建立 `services/models.py`，定義 `ServiceResult` dataclass
- [ ] **開發 1.3**：建立 `services/exceptions.py`，定義自定義例外類別
- [ ] **commit：嚴格按照既有的 commit message 風格撰寫 commit 訊息**
- [ ] **把完成的項目打勾，若有必要調整後面的步驟就更新 md 檔**

#### Task 2: 實作認證業務邏輯
- [ ] **開發 2.1**：實作 `AuthService.authenticate()` 方法
- [ ] **開發 2.2**：搬移密碼驗證邏輯到服務層
- [ ] **開發 2.3**：搬移 JWT token 產生邏輯到服務層
- [ ] **commit：嚴格按照既有的 commit message 風格撰寫 commit 訊息**
- [ ] **把完成的項目打勾，若有必要調整後面的步驟就更新 md 檔**

#### Task 3: 實作註冊業務邏輯
- [ ] **開發 3.1**：實作 `AuthService.register_user()` 方法
- [ ] **開發 3.2**：搬移 email 和密碼驗證邏輯
- [ ] **開發 3.3**：搬移使用者建立邏輯
- [ ] **commit：嚴格按照既有的 commit message 風格撰寫 commit 訊息**
- [ ] **把完成的項目打勾，若有必要調整後面的步驟就更新 md 檔**

#### Task 4: 重構 View 層
- [ ] **開發 4.1**：修改 `login_view()` 呼叫服務層方法
- [ ] **開發 4.2**：修改 `register_view()` 呼叫服務層方法
- [ ] **開發 4.3**：簡化 view 層邏輯，只保留 HTTP 處理
- [ ] **commit：嚴格按照既有的 commit message 風格撰寫 commit 訊息**
- [ ] **把完成的項目打勾，若有必要調整後面的步驟就更新 md 檔**

#### Task 5: 撰寫完整測試
- [ ] **開發 5.1**：建立 `tests/unit/test_auth_service.py`
- [ ] **開發 5.2**：測試 `authenticate()` 方法（成功/失敗情境）
- [ ] **開發 5.3**：測試 `register_user()` 方法（各種驗證情境）
- [ ] **開發 5.4**：測試邊界情況和例外處理
- [ ] **開發 5.5**：執行整合測試確保 API 行為不變
- [ ] **commit：嚴格按照既有的 commit message 風格撰寫 commit 訊息**
- [ ] **把完成的項目打勾，若有必要調整後面的步驟就更新 md 檔**
