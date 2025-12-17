"""InquirerPy wrapper functions for consistent interactive prompts.

此模組封裝 InquirerPy 的功能，提供統一的介面給 CLI 指令使用。
"""

from typing import Any, Optional

from InquirerPy import inquirer


def prompt_list(
    message: str,
    choices: list[Any],
    default: Optional[Any] = None,
) -> Any:
    """顯示選單讓使用者選擇

    Args:
        message: 提示訊息
        choices: 選項列表
        default: 預設選項

    Returns:
        使用者選擇的項目
    """
    kwargs = {
        "message": message,
        "choices": choices,
    }
    if default is not None:
        kwargs["default"] = default

    return inquirer.select(**kwargs).execute()


def prompt_text(
    message: str,
    default: str = "",
) -> str:
    """提示使用者輸入文字

    Args:
        message: 提示訊息
        default: 預設值

    Returns:
        使用者輸入的文字
    """
    return inquirer.text(
        message=message,
        default=default,
    ).execute()


def prompt_confirm(
    message: str,
    default: bool = True,
) -> bool:
    """詢問使用者確認（Yes/No）

    Args:
        message: 提示訊息
        default: 預設值（True 或 False）

    Returns:
        使用者的選擇（True 或 False）
    """
    return inquirer.confirm(
        message=message,
        default=default,
    ).execute()
