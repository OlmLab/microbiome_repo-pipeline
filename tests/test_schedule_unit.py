from catalog.scopes.longitudinal import schedule_unit


def test_days_from_schedule():
    s = "Patients sampled before FMT and repeatedly after FMT; archive tp values = -10,1,3,14,28,56,90 days relative to FMT"
    assert schedule_unit(s, ["-10", "1", "3", "14", "28", "56", "90"]) == "day"


def test_weeks_from_schedule():
    assert schedule_unit("stool at 0, 12, 28 and 52 weeks", ["0", "12", "28", "52"]) == "week"


def test_rejects_ordinal_codes():
    s = "Same subjects sampled at multiple timepoints per phase (suffix _0/_1/_2 in sample titles), exact day offsets not stated"
    assert schedule_unit(s, ["0", "1", "2"]) is None


def test_rejects_two_units_or_no_overlap():
    assert schedule_unit("at 2 weeks and 6 months", ["2", "6"]) is None
    assert schedule_unit("stool at days 0 and 30", ["1", "2", "3"]) is None
    assert schedule_unit(None, ["1", "2"]) is None
