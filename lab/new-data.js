{
    "collector": "coverage.py 4.0",
    "config": {
        "branch": true,
        "source": ".",
    },
    "collected": "20150711T090600",
    "files": {
        "a/b/c.py": {
            "lines": [1, 2, 3, 4, 5],
            "arcs": [
                "1.2", "3.4", "5.-1"
            ],

            "plugin": "django.coverage",

            "lines": {
                "1": {
                    "tests": [
                        "foo/bar/test.py:TheTest.test_it",
                        "asdasdasd",
                        ],
                    "tests": [17, 34, 23, 12389],
                    },
                "2": {
                    "count": 23,
                    },
                "3": {},
                "4": {},
                "17": {},
                },

            "arcs": {
                "1.2": {},
                "2.3": {},
                "3.-1": {},
            },
        },
    },

    "tests": [
        {
            "file": "a/b/c.py",
            "test": "test_it",
            },
        {
            "file": "a/b/d.py",
            "test": "TheTest.test_it",
            },
    ],

    "runs": [
        {
            // info about each run?
            },
        { ... },
    ],
}
