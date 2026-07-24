"""フォルダ名の安全化（Windows の末尾スペース等でのパス不整合を防ぐ）。"""

from __future__ import annotations

from fullocr.batch import safe_book_name


def test_strips_trailing_space_and_dot():
    # 自炊ファイル名は末尾に空白が入りがち → Windows で mkdir と参照がズレる
    assert safe_book_name("田村 晃 ") == "田村 晃"
    assert safe_book_name("なにか.") == "なにか"
    assert safe_book_name("  前後  ") == "前後"


def test_replaces_illegal_chars():
    assert safe_book_name('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"


def test_keeps_normal_japanese_names():
    assert safe_book_name("極論で語る神経内科 河合 真,香坂 俊") == "極論で語る神経内科 河合 真,香坂 俊"


def test_empty_falls_back():
    assert safe_book_name("   ") == "book"
    assert safe_book_name("...") == "book"
