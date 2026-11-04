from team_rules import validate_team


def test_valid_team_has_no_violations():
    assert validate_team([1, 2], {1, 2, 3}) == []


def test_empty_team_is_rejected():
    assert validate_team([], {1, 2}) == ["a team must have between 1 and 3 monsters"]


def test_too_many_members_are_rejected():
    problems = validate_team([1, 2, 3, 4], {1, 2, 3, 4})
    assert problems == ["a team must have between 1 and 3 monsters"]


def test_duplicates_are_rejected():
    assert validate_team([1, 1], {1}) == ["the same monster cannot be selected twice"]


def test_monsters_owned_by_someone_else_are_rejected():
    problems = validate_team([1, 99], {1})
    assert problems == ["monsters not in your collection: 99"]
