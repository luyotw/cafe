"""測試 XML 問題格式的解析與驗證"""

from pathlib import Path

import pytest

from cafe.core.questions_schema import Question, parse_questions_xml, validate_questions_xml


class TestParseQuestionsXml:
    """測試 parse_questions_xml 函式"""

    def test_parse_valid_xml_with_multiple_questions(self, tmp_path):
        """測試正確解析包含多個問題的合法 XML"""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>What is the expected error behavior?</title>
    <options>
      <option>Return error code and log message</option>
      <option>Throw exception to caller</option>
      <option>Silently retry up to 3 times</option>
    </options>
  </question>
  <question id="2">
    <title>Should it support concurrent access?</title>
    <options>
      <option>Yes, with mutex lock</option>
      <option>No, single-threaded only</option>
    </options>
  </question>
</questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        questions = parse_questions_xml(xml_path)

        assert len(questions) == 2
        assert questions[0].id == "1"
        assert questions[0].title == "What is the expected error behavior?"
        assert questions[0].options == [
            "Return error code and log message",
            "Throw exception to caller",
            "Silently retry up to 3 times",
        ]
        assert questions[1].id == "2"
        assert questions[1].title == "Should it support concurrent access?"
        assert questions[1].options == [
            "Yes, with mutex lock",
            "No, single-threaded only",
        ]

    def test_parse_single_question(self, tmp_path):
        """測試正確解析只有一個問題的 XML"""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>How many retries?</title>
    <options>
      <option>3</option>
      <option>5</option>
    </options>
  </question>
</questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        questions = parse_questions_xml(xml_path)

        assert len(questions) == 1
        assert questions[0].id == "1"
        assert questions[0].title == "How many retries?"
        assert questions[0].options == ["3", "5"]

    def test_parse_xml_without_declaration(self, tmp_path):
        """測試解析沒有 XML 宣告的檔案"""
        xml_content = """<questions>
  <question id="1">
    <title>Test question?</title>
    <options>
      <option>Option A</option>
    </options>
  </question>
</questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        questions = parse_questions_xml(xml_path)

        assert len(questions) == 1
        assert questions[0].title == "Test question?"

    def test_parse_xml_with_whitespace_in_text(self, tmp_path):
        """測試解析選項文字前後有空白的情況"""
        xml_content = """<questions>
  <question id="1">
    <title>  Question with spaces  </title>
    <options>
      <option>  Option with spaces  </option>
    </options>
  </question>
</questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        questions = parse_questions_xml(xml_path)

        # Should strip whitespace
        assert questions[0].title == "Question with spaces"
        assert questions[0].options == ["Option with spaces"]

    def test_parse_nonexistent_file_raises_error(self, tmp_path):
        """測試解析不存在的檔案時拋出例外"""
        xml_path = tmp_path / "nonexistent.xml"

        with pytest.raises(FileNotFoundError):
            parse_questions_xml(xml_path)

    def test_parse_malformed_xml_raises_error(self, tmp_path):
        """測試解析格式錯誤的 XML 時拋出例外"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text("<questions><unclosed", encoding="utf-8")

        with pytest.raises(Exception):
            parse_questions_xml(xml_path)


class TestValidateQuestionsXml:
    """測試 validate_questions_xml 函式"""

    def test_valid_xml_returns_true(self, tmp_path):
        """測試合法 XML 回傳 True"""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>Test?</title>
    <options>
      <option>A</option>
      <option>B</option>
    </options>
  </question>
</questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        assert validate_questions_xml(xml_path) is True

    def test_missing_root_questions_tag(self, tmp_path):
        """測試缺少 root <questions> 標籤回傳 False"""
        xml_content = """<root>
  <question id="1">
    <title>Test?</title>
    <options><option>A</option></options>
  </question>
</root>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        assert validate_questions_xml(xml_path) is False

    def test_empty_questions_tag(self, tmp_path):
        """測試空的 <questions> 標籤回傳 False"""
        xml_content = """<questions></questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        assert validate_questions_xml(xml_path) is False

    def test_question_missing_title(self, tmp_path):
        """測試 question 缺少 title 回傳 False"""
        xml_content = """<questions>
  <question id="1">
    <options><option>A</option></options>
  </question>
</questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        assert validate_questions_xml(xml_path) is False

    def test_question_with_empty_title(self, tmp_path):
        """測試 question 的 title 為空字串回傳 False"""
        xml_content = """<questions>
  <question id="1">
    <title>   </title>
    <options><option>A</option></options>
  </question>
</questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        assert validate_questions_xml(xml_path) is False

    def test_question_missing_options(self, tmp_path):
        """測試 question 缺少 options 回傳 False"""
        xml_content = """<questions>
  <question id="1">
    <title>Test?</title>
  </question>
</questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        assert validate_questions_xml(xml_path) is False

    def test_question_with_empty_options(self, tmp_path):
        """測試 question 的 options 為空回傳 False"""
        xml_content = """<questions>
  <question id="1">
    <title>Test?</title>
    <options></options>
  </question>
</questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        assert validate_questions_xml(xml_path) is False

    def test_malformed_xml_returns_false(self, tmp_path):
        """測試格式錯誤的 XML 回傳 False"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text("<questions><broken", encoding="utf-8")

        assert validate_questions_xml(xml_path) is False

    def test_nonexistent_file_returns_false(self, tmp_path):
        """測試不存在的檔案回傳 False"""
        xml_path = tmp_path / "nonexistent.xml"

        assert validate_questions_xml(xml_path) is False

    def test_empty_file_returns_false(self, tmp_path):
        """測試空檔案回傳 False"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text("", encoding="utf-8")

        assert validate_questions_xml(xml_path) is False

    def test_option_with_empty_text_is_skipped(self, tmp_path):
        """測試選項文字為空時被跳過，如果所有選項都是空的則回傳 False"""
        xml_content = """<questions>
  <question id="1">
    <title>Test?</title>
    <options>
      <option>   </option>
    </options>
  </question>
</questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        assert validate_questions_xml(xml_path) is False

    def test_multiple_questions_one_invalid(self, tmp_path):
        """測試多個問題中有一個不合法時回傳 False"""
        xml_content = """<questions>
  <question id="1">
    <title>Valid question?</title>
    <options><option>A</option></options>
  </question>
  <question id="2">
    <title></title>
    <options><option>B</option></options>
  </question>
</questions>"""
        xml_path = tmp_path / "questions.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        assert validate_questions_xml(xml_path) is False


class TestQuestionDataclass:
    """測試 Question dataclass"""

    def test_question_creation(self):
        """測試建立 Question 物件"""
        q = Question(id="1", title="Test?", options=["A", "B"])
        assert q.id == "1"
        assert q.title == "Test?"
        assert q.options == ["A", "B"]

    def test_question_equality(self):
        """測試 Question 物件相等比較"""
        q1 = Question(id="1", title="Test?", options=["A", "B"])
        q2 = Question(id="1", title="Test?", options=["A", "B"])
        assert q1 == q2
