from utils.skills_lexicon import is_technical, split_skills


class TestIsTechnical:
    def test_programming_language(self):
        assert is_technical("Python") is True
        assert is_technical("JavaScript") is True
        assert is_technical("Go") is True

    def test_framework(self):
        assert is_technical("React") is True
        assert is_technical("Django") is True
        assert is_technical("Spring Boot") is True

    def test_cloud_tool(self):
        assert is_technical("Docker") is True
        assert is_technical("Kubernetes") is True
        assert is_technical("AWS") is True

    def test_database(self):
        assert is_technical("PostgreSQL") is True
        assert is_technical("MongoDB") is True
        assert is_technical("Redis") is True

    def test_non_technical_excluded(self):
        assert is_technical("Agile") is False
        assert is_technical("Scrum") is False
        assert is_technical("Teamwork") is False
        assert is_technical("Communication") is False
        assert is_technical("Leadership") is False
        assert is_technical("Problem Solving") is False
        assert is_technical("Design Systems") is False
        assert is_technical("User Research") is False
        assert is_technical("Prototyping") is False
        assert is_technical("scheduling") is False

    def test_case_insensitive(self):
        assert is_technical("python") is True
        assert is_technical("PYTHON") is True
        assert is_technical("PyThOn") is True

    def test_empty_string(self):
        assert is_technical("") is False

    def test_whitespace_handling(self):
        assert is_technical("  Python  ") is True


class TestSplitSkills:
    def test_all_technical(self):
        tech, soft = split_skills(["Python", "Django", "PostgreSQL"])
        assert tech == ["Python", "Django", "PostgreSQL"]
        assert soft == []

    def test_all_non_technical(self):
        tech, soft = split_skills(["Agile", "Scrum", "Teamwork"])
        assert tech == []
        assert soft == ["Agile", "Scrum", "Teamwork"]

    def test_mixed(self):
        tech, soft = split_skills(["Python", "Agile", "Django", "Scrum"])
        assert tech == ["Python", "Django"]
        assert soft == ["Agile", "Scrum"]

    def test_empty_list(self):
        tech, soft = split_skills([])
        assert tech == []
        assert soft == []

    def test_preserves_original_casing(self):
        tech, soft = split_skills(["PYTHON", "agile", "Django"])
        assert tech == ["PYTHON", "Django"]
        assert soft == ["agile"]
