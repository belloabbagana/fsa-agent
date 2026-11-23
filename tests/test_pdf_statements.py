"""Known-answer tests for the PDF statement parser, using made-up lines in the same layout."""
from fsa.extract.pdf_statements import parse_line, parse_statement_page

PAGE = """Consolidated and separate statements of profit or loss
For The Year Ended 31 December 2030
GROUP COMPANY
Year ended Year ended Year ended Year ended
31/12/2030 31/12/2029 31/12/2030 31/12/2029
Notes ₦'million ₦'million ₦'million ₦'million
Revenue 5 1,000 800 600 500
Cost of sales 7 (400) (350) (250) (200)
Gross profit 600 450 350 300
Associate income 18.3 12 - - -
Write off & impairment of property, plant,
equipment and intangibles 12 (3) (2) - -
Earnings per share, basic and diluted (Naira) 13 1.50 1.25 1.10 1.05
The accompanying notes form an integral part of these statements
999
"""


def test_label_note_and_brackets():
    label, note, values, problems = parse_line("Cost of sales 7 (400) (350) (250) (200)")
    assert (label, note) == ("Cost of sales", "7")
    assert [v for v, _ in values] == [-400, -350, -250, -200] and not problems


def test_line_without_note_and_dotted_note():
    assert parse_line("Gross profit 600 450 350 300")[1] is None
    assert parse_line("Associate income 18.3 12 - - -")[1] == "18.3"


def test_dash_is_a_printed_nil_not_a_missing_value():
    values = parse_line("Associate income 18.3 12 - - -")[2]
    assert values[1] == (0.0, "reported_nil") and values[0] == (12.0, "ok")
    assert parse_line("Receivables 31.2 ‑ 1,045,575 1,713,814 2,733,412")[2][0] == (0.0, "reported_nil")  # non-breaking hyphen


def test_unlabelled_subtotal_and_short_lines():
    assert parse_line("1,014,921 503,247 708,842 1,027,217")[0] == ""
    assert parse_line("Profit for the year attributable to:") is None
    label, note, values, problems = parse_line("As at 31 December 2030")
    assert values == [] and problems            # too few columns: never guessed


def test_full_page_dates_wrapped_label_and_footer():
    dates, unit, lines = parse_statement_page(PAGE, page=1)
    assert [str(d) for d in dates] == ["2030-12-31", "2029-12-31", "2030-12-31", "2029-12-31"]
    assert unit == "NGN_million"
    labels = [l.label for l in lines]
    assert labels[0] == "Revenue" and len(lines) == 6          # footer after "accompanying notes" ignored
    assert lines[4].label.startswith("Write off") and "intangibles" in lines[4].label   # wrapped label joined
    assert lines[-1].values[0][0] == 1.50


def test_two_digit_year_dates_and_multi_note_labels():
    text = """Group Company
Year ended Year ended Year ended Year ended
31/12/23 31/12/22 31/12/23 31/12/22
Notes ₦’million ₦’million ₦’million ₦’million
Depreciation & amortisation 15, 16 & 17 151,160 120,390 53,811 60,265
Interest & dividend income 10.1 (27,405) (38,715) (197,093) (78,247)
Share of income from associate 18.3 (1,231) (759) — —
Provisions 10,625 2,147 (990) 1,261
"""
    dates, unit, lines = parse_statement_page(text, page=1)
    assert [str(d) for d in dates] == ["2023-12-31", "2022-12-31", "2023-12-31", "2022-12-31"]
    assert (lines[0].label, lines[0].note_ref) == ("Depreciation & amortisation", "15, 16 & 17")
    assert lines[1].note_ref == "10.1" and lines[3].note_ref is None
    assert lines[2].values[2] == (0.0, "reported_nil")      # an em dash is a printed nil


def test_label_with_text_above_numbers_is_joined():
    dates, unit, lines = parse_statement_page(PAGE, page=1)
    assert any(l.label.startswith("Write off & impairment of property, plant, equipment") for l in lines)


def test_negative_number_with_only_one_bracket_is_negative():
    from fsa.extract.pdf_statements import parse_value
    assert parse_value("27,557)") == (-27557.0, "ok")        # stray space split the bracket from the digits
    assert parse_value("(27,557") == (-27557.0, "ok")
    assert parse_value("27,557") == (27557.0, "ok")
