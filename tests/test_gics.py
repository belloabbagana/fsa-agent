from fsa.classify.gics_structure import check, counts


def test_structure_has_the_right_number_of_groups():
    assert counts() == (11, 25, 74)          # 11 sectors, 25 industry groups, 74 industries


def test_names_must_exist_and_nest_correctly():
    assert check("Materials", "Materials", "Construction Materials") is None
    assert check("Consumer Staples", "Food, Beverage & Tobacco", "Food Products") is None
    assert "unknown sector" in check("Agriculture", "Crop Production", "Crop Production")          # an exchange label is not GICS
    assert "does not belong" in check("Materials", "Materials", "Food Products")                    # real industry, wrong parent
    assert "does not belong" in check("Consumer Staples", "Materials", "Construction Materials")
