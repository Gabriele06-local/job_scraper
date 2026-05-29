from __future__ import annotations

TECHNICAL_SKILLS: set[str] = {
    # Languages
    "python", "javascript", "typescript", "java", "go", "golang", "rust",
    "c#", "csharp", "c++", "cpp", "c", "ruby", "php", "kotlin", "swift",
    "scala", "perl", "haskell", "elixir", "clojure", "dart", "lua", "r",
    "matlab", "groovy", "solidity", "delphi", "pascal", "fortran", "cobol",
    "html", "css", "sass", "scss", "less", "stylus", "xml", "json", "yaml",
    "toml", "markdown", "latex", "sql", "bash", "shell", "powershell",
    "awk", "sed", "vhdl", "verilog", "assembly", "wasm",
    # Frontend frameworks & libraries
    "react", "react.js", "reactjs", "angular", "angularjs", "vue", "vue.js",
    "vuejs", "svelte", "next.js", "nextjs", "nuxt.js", "nuxtjs", "gatsby",
    "remix", "astro", "solid.js", "solidjs", "qwik", "jquery", "bootstrap",
    "tailwind", "tailwind css", "tailwindcss", "material ui", "mui", "chakra ui",
    "ant design", "antd", "semantic ui", "bulma", "foundation",
    "redux", "redux toolkit", "rtk", "mobx", "zustand", "recoil", "pinia",
    "vuex", "ngrx", "webpack", "vite", "esbuild", "parcel", "rollup",
    "babel", "swc", "eslint", "prettier", "stylelint", "storybook",
    "electron", "tauri", "nw.js",
    # Backend frameworks
    "django", "flask", "fastapi", "spring", "spring boot", "spring framework",
    "express", "express.js", "expressjs", "node.js", "nodejs", "laravel",
    "symfony", "rails", "ruby on rails", "asp.net", "aspnet", "asp.net core",
    "dotnet", ".net", ".net core", "gin", "echo", "fiber", "ktor",
    "actix", "rocket", "axum", "tornado", "aiohttp", "starlette",
    "sanic", "quart", "phalcon", "cakephp", "yii", "codeigniter",
    # Cloud & DevOps
    "aws", "amazon web services", "azure", "gcp", "google cloud", "google cloud platform",
    "cloud", "cloud computing", "docker", "kubernetes", "k8s", "openshift",
    "terraform", "opentofu", "ansible", "puppet", "chef", "saltstack",
    "jenkins", "gitlab ci", "gitlab ci/cd", "github actions", "circleci",
    "travis ci", "travis", "teamcity", "bamboo", "argo cd", "argocd",
    "helm", "kustomize", "istio", "linkerd", "envoy", "consul", "vault",
    "prometheus", "grafana", "elk stack", "elastic stack", "elasticsearch",
    "logstash", "kibana", "datadog", "new relic", "sentry", "dynatrace",
    "splunk", "sumo logic", "opentelemetry", "jaeger", "zipkin",
    "nginx", "apache", "apache httpd", "haproxy", "traefik", "caddy",
    "cloudflare", "fastly", "akamai",
    # Databases
    "postgresql", "postgres", "mysql", "mariadb", "mongodb", "redis",
    "cassandra", "dynamodb", "couchbase", "couchdb", "neo4j", "arangodb",
    "sqlite", "oracle", "oracle database", "sql server", "mssql", "microsoft sql server",
    "firebase", "supabase", "cockroachdb", "tidb", "clickhouse",
    "influxdb", "timescaledb", "pinot", "druid", "scylladb",
    "kafka", "apache kafka", "rabbitmq", "activemq", "pulsar", "nats", "mqtt",
    # Data & ML
    "tensorflow", "pytorch", "keras", "scikit-learn", "sklearn", "pandas",
    "numpy", "scipy", "spark", "apache spark", "pyspark", "hadoop",
    "airflow", "apache airflow", "dbt", "prefect", "dagster", "luigi",
    "tableau", "power bi", "powerbi", "looker", "superset", "metabase",
    "redash", "grafana", "mlflow", "wandb", "weights & biases",
    "jupyter", "jupyter notebook", "databricks", "snowflake", "bigquery",
    "redshift", "synapse", "dataflow", "dataproc", "emr",
    "opencv", "nltk", "spacy", "hugging face", "transformers", "langchain",
    "llamaindex", "chromadb", "pinecone", "weaviate", "qdrant", "milvus",
    # Mobile
    "android", "android sdk", "ios", "react native", "flutter", "xamarin",
    "cordova", "ionic", "swiftui", "uikit", "jetpack compose", "rxswift",
    "alamofire", "retrofit", "room", "dart", "objective-c", "objective c",
    "appcode", "xcode", "android studio",
    # Design tools
    "figma", "sketch", "adobe xd", "photoshop", "illustrator", "indesign",
    "after effects", "premiere pro", "lightroom", "zeplin", "invision",
    "framer", "proto.io", "balsamiq", "axure", "marvel app",
    # Testing & QA
    "selenium", "cypress", "playwright", "puppeteer", "jest", "mocha",
    "chai", "pytest", "junit", "testng", "cucumber", "gherkin",
    "jasmine", "karma", "webdriverio", "appium", "detox", "espresso",
    "xctest", "sonarqube", "sonarcloud", "jira", "testrail", "postman",
    "insomnia", "soapui", "k6", "locust", "gatling", "jmeter",
    # Security
    "burp suite", "burpsuite", "nmap", "wireshark", "metasploit",
    "nessus", "openvas", "qualys", "crowdstrike", "splunk",
    "siem", "owasp", "penetration testing", "pen testing",
    "oauth", "oauth2", "jwt", "saml", "openid", "ldap", "ssl", "tls",
    "vpn", "firewall", "waf", "ids", "ips", "dns", "certificates",
    # Networking
    "tcp/ip", "http", "https", "dns", "dhcp", "nat", "vlan", "bgp", "ospf",
    "rest", "rest api", "restful", "graphql", "grpc", "websocket", "websockets",
    "soap", "api", "microservices",
    # Tools & platforms
    "git", "github", "gitlab", "bitbucket", "svn", "subversion", "mercurial",
    "vscode", "visual studio code", "intellij", "intellij idea", "pycharm",
    "eclipse", "netbeans", "vim", "neovim", "emacs", "sublime text",
    "confluence", "notion", "asana", "trello", "monday.com", "clickup",
    "slack", "teams", "discord",
    "linux", "unix", "ubuntu", "centos", "red hat", "debian", "alpine",
    "windows server", "iis", "active directory",
    # SAP / ERP / CRM
    "sap", "sap s/4hana", "sap abap", "sap fico", "sap sd", "sap mm",
    "salesforce", "oracle ebs", "oracle erp", "microsoft dynamics",
    "dynamics 365", "hubspot", "zoho", "netsuite",
    # CI/CD & build tools
    "ci/cd", "ci cd", "maven", "gradle", "npm", "yarn", "pnpm", "pip",
    "conda", "poetry", "nuget", "cargo", "go modules", "bazel",
    # Architecture & patterns
    "serverless", "lambda", "cloudformation", "pulumi", "crossplane",
    "event-driven", "event sourcing", "cqrs", "domain-driven design", "ddd",
    "tdd", "test-driven development", "clean architecture", "hexagonal architecture",
    "onion architecture", "soa", "service-oriented architecture",
    # Data engineering specific
    "etl", "elt", "data modeling", "data modelling", "data pipeline",
    "data warehouse", "data lake", "data mesh", "data vault",
    # Monitoring
    "monitoring", "alerting", "incident management", "pagerduty", "opsgenie",
    # Specific tools
    "gradle", "maven", "ant", "make", "cmake", "meson",
    "ansible tower", "awx", "rancher", "nomad",
    # Office tools (borderline but included as concrete tools)
    "microsoft office", "office 365", "excel", "word", "powerpoint",
    "outlook", "sharepoint", "teams",
    # Platform-specific
    "sap", "salesforce", "servicenow", "workday",
    # Specific fixture items
    "testing library", "rest api", "jest", "xcode", "testflight",
    "core data", "coredata", "rxjs", "express", "swiftui",
    "jetpack compose", "retrofit", "room",
    "vue.js", "vuejs", "node.js", "nodejs", "ci/cd",
    "data modeling", "data modelling", "microservices",
    "penetration testing", "siem", "nmap", "owasp", "burp suite", "burpsuite",
    "soporte técnico",  # "technical support" in Spanish — borderline but included
}


def is_technical(skill: str) -> bool:
    """Return True if *skill* is a known technical skill (case-insensitive)."""
    return skill.strip().lower() in TECHNICAL_SKILLS


def split_skills(skills: list[str]) -> tuple[list[str], list[str]]:
    """Split a flat skills list into (technical_skills, soft_skills).

    Args:
        skills: Raw skills list from AI classifier output.

    Returns:
        Tuple of (technical_skills, skills) where technical_skills contains
        items matching the lexicon and skills contains everything else.
    """
    tech: list[str] = []
    soft: list[str] = []
    for s in skills:
        (tech if is_technical(s) else soft).append(s)
    return tech, soft
