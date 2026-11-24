"""
Generator for ~100 Synthetic Golden Memory Eval Cases across 8 Categories (Phase 0.5 - E3).
Categories:
1. fact_retrieval (20 cases)
2. supersede_update (15 cases)
3. reaffirmation (10 cases)
4. cross_speaker_conflict (10 cases)
5. abstention (15 cases)
6. temporal_validity (10 cases)
7. sensitivity_secrets (10 cases)
8. code_and_work_lanes (10 cases)
"""
from pathlib import Path
import yaml

CASES_DIR = Path(__file__).parent / "cases"
CASES_DIR.mkdir(parents=True, exist_ok=True)

# 1. FACT RETRIEVAL (20 cases)
FACT_CASES = [
    {
        "id": "fact-01-primary-editor",
        "category": "fact_retrieval",
        "setup": [{"content": "I use Neovim as my primary code editor.", "occurred_at": "-5d"}],
        "query": {"text": "What is my primary code editor?"},
        "expect": {"answer_contains_any": ["Neovim"], "cites_setup": [0]},
    },
    {
        "id": "fact-02-dog-name",
        "category": "fact_retrieval",
        "setup": [{"content": "My golden retriever is named Barnaby.", "occurred_at": "-10d"}],
        "query": {"text": "What is my dog's name?"},
        "expect": {"answer_contains_any": ["Barnaby"], "cites_setup": [0]},
    },
    {
        "id": "fact-03-city-residence",
        "category": "fact_retrieval",
        "setup": [{"content": "I live in Bengaluru near Indiranagar.", "occurred_at": "-20d"}],
        "query": {"text": "Where do I live?"},
        "expect": {"answer_contains_any": ["Bengaluru", "Indiranagar"], "cites_setup": [0]},
    },
    {
        "id": "fact-04-coffee-preference",
        "category": "fact_retrieval",
        "setup": [{"content": "I drink black pour-over coffee every morning.", "occurred_at": "-3d"}],
        "query": {"text": "How do I take my coffee?"},
        "expect": {"answer_contains_any": ["black", "pour-over"], "cites_setup": [0]},
    },
    {
        "id": "fact-05-project-db",
        "category": "fact_retrieval",
        "setup": [{"content": "Lumen uses PostgreSQL 17 with pgvector for memory persistence.", "occurred_at": "-7d"}],
        "query": {"text": "What database does Lumen use?"},
        "expect": {"answer_contains_any": ["PostgreSQL 17", "Postgres"], "cites_setup": [0]},
    },
    {
        "id": "fact-06-keyboard-layout",
        "category": "fact_retrieval",
        "setup": [{"content": "I type on a Corne split mechanical keyboard with Colemak-DH layout.", "occurred_at": "-15d"}],
        "query": {"text": "What keyboard layout do I use?"},
        "expect": {"answer_contains_any": ["Colemak", "Corne"], "cites_setup": [0]},
    },
    {
        "id": "fact-07-brother-name",
        "category": "fact_retrieval",
        "setup": [{"content": "My younger brother Anshul is studying computer science at IIT Delhi.", "occurred_at": "-30d"}],
        "query": {"text": "What is my brother's name and college?"},
        "expect": {"answer_contains_any": ["Anshul", "IIT Delhi"], "cites_setup": [0]},
    },
    {
        "id": "fact-08-laptop-hardware",
        "category": "fact_retrieval",
        "setup": [{"content": "My development machine is an Apple MacBook Air M3 with 16GB unified memory.", "occurred_at": "-12d"}],
        "query": {"text": "What laptop hardware do I have?"},
        "expect": {"answer_contains_any": ["MacBook Air M3", "16GB"], "cites_setup": [0]},
    },
    {
        "id": "fact-09-favorite-tea",
        "category": "fact_retrieval",
        "setup": [{"content": "In the afternoons I always brew Japanese Sencha green tea.", "occurred_at": "-4d"}],
        "query": {"text": "What tea do I like in the afternoon?"},
        "expect": {"answer_contains_any": ["Sencha", "green tea"], "cites_setup": [0]},
    },
    {
        "id": "fact-10-work-role",
        "category": "fact_retrieval",
        "setup": [{"content": "I work as a Staff Systems Architect at CloudScale.", "occurred_at": "-40d"}],
        "query": {"text": "What is my job title and company?"},
        "expect": {"answer_contains_any": ["Staff Systems Architect", "CloudScale"], "cites_setup": [0]},
    },
    {
        "id": "fact-11-car-model",
        "category": "fact_retrieval",
        "setup": [{"content": "I drive a white Hyundai Ioniq 5 electric car.", "occurred_at": "-25d"}],
        "query": {"text": "What car do I drive?"},
        "expect": {"answer_contains_any": ["Ioniq 5", "Hyundai"], "cites_setup": [0]},
    },
    {
        "id": "fact-12-mother-tongue",
        "category": "fact_retrieval",
        "setup": [{"content": "My mother tongue is Hindi and I also speak fluent English.", "occurred_at": "-50d"}],
        "query": {"text": "What is my native language?"},
        "expect": {"answer_contains_any": ["Hindi"], "cites_setup": [0]},
    },
    {
        "id": "fact-13-guitar-hobby",
        "category": "fact_retrieval",
        "setup": [{"content": "On weekends I practice fingerstyle guitar on my Taylor 214ce.", "occurred_at": "-6d"}],
        "query": {"text": "What acoustic guitar do I play?"},
        "expect": {"answer_contains_any": ["Taylor 214ce", "guitar"], "cites_setup": [0]},
    },
    {
        "id": "fact-14-allergy",
        "category": "fact_retrieval",
        "setup": [{"content": "I am allergic to peanuts and carry an EpiPen.", "occurred_at": "-60d"}],
        "query": {"text": "Do I have any food allergies?"},
        "expect": {"answer_contains_any": ["peanuts", "allergic"], "cites_setup": [0]},
    },
    {
        "id": "fact-15-running-goal",
        "category": "fact_retrieval",
        "setup": [{"content": "I am training for the Bengaluru Half Marathon with a target time under 1h 45m.", "occurred_at": "-8d"}],
        "query": {"text": "What race am I training for?"},
        "expect": {"answer_contains_any": ["Bengaluru Half Marathon", "Half Marathon"], "cites_setup": [0]},
    },
    {
        "id": "fact-16-cloud-provider",
        "category": "fact_retrieval",
        "setup": [{"content": "Our production backend infrastructure runs in AWS ap-south-1 region.", "occurred_at": "-14d"}],
        "query": {"text": "What AWS region hosts our production servers?"},
        "expect": {"answer_contains_any": ["ap-south-1", "AWS"], "cites_setup": [0]},
    },
    {
        "id": "fact-17-favorite-author",
        "category": "fact_retrieval",
        "setup": [{"content": "My favorite science fiction author is Ted Chiang.", "occurred_at": "-18d"}],
        "query": {"text": "Who is my favorite sci-fi writer?"},
        "expect": {"answer_contains_any": ["Ted Chiang"], "cites_setup": [0]},
    },
    {
        "id": "fact-18-shoe-size",
        "category": "fact_retrieval",
        "setup": [{"content": "My running shoes are UK size 10.", "occurred_at": "-9d"}],
        "query": {"text": "What shoe size do I wear for running?"},
        "expect": {"answer_contains_any": ["10", "UK 10"], "cites_setup": [0]},
    },
    {
        "id": "fact-19-preferred-terminal",
        "category": "fact_retrieval",
        "setup": [{"content": "I use Ghostty as my terminal emulator across macOS and Linux.", "occurred_at": "-11d"}],
        "query": {"text": "What terminal emulator do I use?"},
        "expect": {"answer_contains_any": ["Ghostty"], "cites_setup": [0]},
    },
    {
        "id": "fact-20-monolith-framework",
        "category": "fact_retrieval",
        "setup": [{"content": "Lumen core backend service is written in Python 3.12 using FastAPI and AsyncPG.", "occurred_at": "-16d"}],
        "query": {"text": "What web framework powers Lumen backend?"},
        "expect": {"answer_contains_any": ["FastAPI", "AsyncPG"], "cites_setup": [0]},
    },
]

# 2. SUPERSEDE & UPDATE (15 cases)
SUPERSEDE_CASES = [
    {
        "id": "supersede-01-editor-switch",
        "category": "supersede_update",
        "setup": [
            {"content": "I use VS Code for coding.", "occurred_at": "-30d"},
            {"content": "I switched from VS Code to Neovim as my main editor.", "occurred_at": "-2d"},
        ],
        "query": {"text": "What is my current code editor?"},
        "expect": {
            "answer_contains_any": ["Neovim"],
            "answer_excludes_any": ["VS Code"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-02-db-upgrade",
        "category": "supersede_update",
        "setup": [
            {"content": "Lumen runs on PostgreSQL 16.", "occurred_at": "-20d"},
            {"content": "We upgraded Lumen primary database to PostgreSQL 17.", "occurred_at": "-3d"},
        ],
        "query": {"text": "What PostgreSQL version does Lumen use?"},
        "expect": {
            "answer_contains_any": ["17", "PostgreSQL 17"],
            "answer_excludes_any": ["PostgreSQL 16"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-03-city-move",
        "category": "supersede_update",
        "setup": [
            {"content": "I live in Mumbai near Bandra.", "occurred_at": "-180d"},
            {"content": "I moved from Mumbai to Bengaluru last month.", "occurred_at": "-15d"},
        ],
        "query": {"text": "Where do I currently live?"},
        "expect": {
            "answer_contains_any": ["Bengaluru"],
            "answer_excludes_any": ["Mumbai"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-04-car-replacement",
        "category": "supersede_update",
        "setup": [
            {"content": "I drive a Honda Civic.", "occurred_at": "-100d"},
            {"content": "I sold my Civic and bought a Hyundai Ioniq 5 electric car.", "occurred_at": "-10d"},
        ],
        "query": {"text": "What car do I have now?"},
        "expect": {
            "answer_contains_any": ["Ioniq 5", "Hyundai"],
            "answer_excludes_any": ["Honda Civic"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-05-job-promotion",
        "category": "supersede_update",
        "setup": [
            {"content": "I am a Senior Software Engineer at Stripe.", "occurred_at": "-120d"},
            {"content": "I got promoted to Staff Engineer at Stripe.", "occurred_at": "-5d"},
        ],
        "query": {"text": "What is my current role at Stripe?"},
        "expect": {
            "answer_contains_any": ["Staff Engineer"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-06-diet-change",
        "category": "supersede_update",
        "setup": [
            {"content": "I eat chicken and eggs regularly.", "occurred_at": "-90d"},
            {"content": "I decided to become strictly vegetarian from this month.", "occurred_at": "-4d"},
        ],
        "query": {"text": "What are my current dietary habits?"},
        "expect": {
            "answer_contains_any": ["vegetarian"],
            "answer_excludes_any": ["chicken"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-07-python-version",
        "category": "supersede_update",
        "setup": [
            {"content": "Our microservices run on Python 3.10.", "occurred_at": "-50d"},
            {"content": "We upgraded all microservices to Python 3.12.", "occurred_at": "-7d"},
        ],
        "query": {"text": "What Python version do we target for microservices?"},
        "expect": {
            "answer_contains_any": ["Python 3.12", "3.12"],
            "answer_excludes_any": ["3.10"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-08-phone-upgrade",
        "category": "supersede_update",
        "setup": [
            {"content": "I use an iPhone 13 Mini as my personal phone.", "occurred_at": "-80d"},
            {"content": "I bought a Google Pixel 9 Pro to replace my old iPhone.", "occurred_at": "-8d"},
        ],
        "query": {"text": "What is my current personal smartphone?"},
        "expect": {
            "answer_contains_any": ["Pixel 9 Pro", "Google Pixel"],
            "answer_excludes_any": ["iPhone 13"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-09-negation-docker",
        "category": "supersede_update",
        "setup": [
            {"content": "I use Docker Desktop for local container management.", "occurred_at": "-40d"},
            {"content": "I no longer use Docker Desktop, I switched entirely to OrbStack.", "occurred_at": "-6d"},
        ],
        "query": {"text": "What tool do I use for local containers?"},
        "expect": {
            "answer_contains_any": ["OrbStack"],
            "answer_excludes_any": ["Docker Desktop"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-10-branch-name",
        "category": "supersede_update",
        "setup": [
            {"content": "Our default git branch is named master.", "occurred_at": "-200d"},
            {"content": "We renamed our repository default branch to main.", "occurred_at": "-12d"},
        ],
        "query": {"text": "What is our default git branch?"},
        "expect": {
            "answer_contains_any": ["main"],
            "answer_excludes_any": ["master"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-11-gym-membership",
        "category": "supersede_update",
        "setup": [
            {"content": "I work out at Cult.fit Indiranagar.", "occurred_at": "-70d"},
            {"content": "I cancelled Cult.fit and joined Gold's Gym.", "occurred_at": "-9d"},
        ],
        "query": {"text": "Which gym do I go to?"},
        "expect": {
            "answer_contains_any": ["Gold's Gym"],
            "answer_excludes_any": ["Cult.fit"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-12-project-status",
        "category": "supersede_update",
        "setup": [
            {"content": "Project Apollo is in active development phase.", "occurred_at": "-60d"},
            {"content": "Project Apollo launched to general availability today.", "occurred_at": "-1d"},
        ],
        "query": {"text": "What is the status of Project Apollo?"},
        "expect": {
            "answer_contains_any": ["general availability", "launched"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-13-cloud-storage",
        "category": "supersede_update",
        "setup": [
            {"content": "We store backups in AWS S3 Standard.", "occurred_at": "-45d"},
            {"content": "We migrated our database backups to Cloudflare R2 for zero egress fees.", "occurred_at": "-3d"},
        ],
        "query": {"text": "Where do we store database backups?"},
        "expect": {
            "answer_contains_any": ["Cloudflare R2", "R2"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-14-preferred-shell",
        "category": "supersede_update",
        "setup": [
            {"content": "I use bash as my login shell.", "occurred_at": "-90d"},
            {"content": "I changed my default shell to zsh with starship prompt.", "occurred_at": "-4d"},
        ],
        "query": {"text": "What is my login shell?"},
        "expect": {
            "answer_contains_any": ["zsh"],
            "answer_excludes_any": ["bash"],
            "cites_setup": [1],
        },
    },
    {
        "id": "supersede-15-sleep-schedule",
        "category": "supersede_update",
        "setup": [
            {"content": "I usually sleep at 1:00 AM.", "occurred_at": "-35d"},
            {"content": "I fixed my circadian rhythm and now sleep by 10:30 PM.", "occurred_at": "-5d"},
        ],
        "query": {"text": "What time do I go to bed now?"},
        "expect": {
            "answer_contains_any": ["10:30"],
            "cites_setup": [1],
        },
    },
]

# 3. REAFFIRMATION (10 cases)
REAFFIRMATION_CASES = [
    {
        "id": "reaffirm-01-neovim-reasserted",
        "category": "reaffirmation",
        "setup": [
            {"content": "I love coding in Neovim.", "occurred_at": "-10d"},
            {"content": "Still using Neovim every single day, best editor.", "occurred_at": "-1d"},
        ],
        "query": {"text": "What editor do I use?"},
        "expect": {"answer_contains_any": ["Neovim"]},
    },
    {
        "id": "reaffirm-02-postgres-reasserted",
        "category": "reaffirmation",
        "setup": [
            {"content": "Lumen uses PostgreSQL 17 as its primary database.", "occurred_at": "-8d"},
            {"content": "Postgres 17 continues to be Lumen primary database in production.", "occurred_at": "-2d"},
        ],
        "query": {"text": "What is Lumen primary database?"},
        "expect": {"answer_contains_any": ["Postgres", "PostgreSQL 17"]},
    },
    {
        "id": "reaffirm-03-bengaluru-reasserted",
        "category": "reaffirmation",
        "setup": [
            {"content": "I live in Indiranagar, Bengaluru.", "occurred_at": "-15d"},
            {"content": "Loving the weather here in Indiranagar Bengaluru today.", "occurred_at": "-2d"},
        ],
        "query": {"text": "Where do I stay?"},
        "expect": {"answer_contains_any": ["Bengaluru", "Indiranagar"]},
    },
    {
        "id": "reaffirm-04-dog-barnaby-reasserted",
        "category": "reaffirmation",
        "setup": [
            {"content": "My golden retriever Barnaby is 3 years old.", "occurred_at": "-12d"},
            {"content": "Took my dog Barnaby for a walk in the park.", "occurred_at": "-1d"},
        ],
        "query": {"text": "What is my dog's name?"},
        "expect": {"answer_contains_any": ["Barnaby"]},
    },
    {
        "id": "reaffirm-05-black-coffee-reasserted",
        "category": "reaffirmation",
        "setup": [
            {"content": "I only drink black coffee without sugar.", "occurred_at": "-7d"},
            {"content": "Enjoying another cup of hot black coffee.", "occurred_at": "-1d"},
        ],
        "query": {"text": "Do I take sugar in my coffee?"},
        "expect": {"answer_contains_any": ["black", "without sugar"]},
    },
    {
        "id": "reaffirm-06-ghostty-reasserted",
        "category": "reaffirmation",
        "setup": [
            {"content": "Ghostty is my daily terminal.", "occurred_at": "-9d"},
            {"content": "Ghostty terminal is so fast and smooth.", "occurred_at": "-3d"},
        ],
        "query": {"text": "What terminal do I like?"},
        "expect": {"answer_contains_any": ["Ghostty"]},
    },
    {
        "id": "reaffirm-07-fastapi-backend",
        "category": "reaffirmation",
        "setup": [
            {"content": "We built our API service with FastAPI.", "occurred_at": "-14d"},
            {"content": "FastAPI is our standard backend API framework.", "occurred_at": "-4d"},
        ],
        "query": {"text": "What API framework do we use?"},
        "expect": {"answer_contains_any": ["FastAPI"]},
    },
    {
        "id": "reaffirm-08-brother-anshul",
        "category": "reaffirmation",
        "setup": [
            {"content": "My brother Anshul is in Delhi.", "occurred_at": "-20d"},
            {"content": "Had a video call with my brother Anshul today.", "occurred_at": "-3d"},
        ],
        "query": {"text": "Who is my brother?"},
        "expect": {"answer_contains_any": ["Anshul"]},
    },
    {
        "id": "reaffirm-09-macbook-hardware",
        "category": "reaffirmation",
        "setup": [
            {"content": "My laptop is an Apple MacBook Air M3.", "occurred_at": "-16d"},
            {"content": "Developing locally on my MacBook Air M3 is super efficient.", "occurred_at": "-2d"},
        ],
        "query": {"text": "What laptop do I develop on?"},
        "expect": {"answer_contains_any": ["MacBook Air M3"]},
    },
    {
        "id": "reaffirm-10-sencha-tea",
        "category": "reaffirmation",
        "setup": [
            {"content": "I drink Japanese Sencha green tea.", "occurred_at": "-11d"},
            {"content": "Brewed a fresh pot of Sencha tea.", "occurred_at": "-1d"},
        ],
        "query": {"text": "What green tea do I drink?"},
        "expect": {"answer_contains_any": ["Sencha"]},
    },
]

# 4. CROSS SPEAKER CONFLICT (10 cases)
CONFLICT_CASES = [
    {
        "id": "conflict-01-database-choice",
        "category": "cross_speaker_conflict",
        "setup": [
            {"content": "I decided Lumen must use PostgreSQL 17 for ACID transactions.", "occurred_at": "-4d", "speaker": "user"},
            {"content": "Rahul says Lumen should migrate to MongoDB for document flexibility.", "occurred_at": "-1d", "speaker": "Rahul"},
        ],
        "query": {"text": "What database did I decide on for Lumen?"},
        "expect": {
            "answer_contains_any": ["PostgreSQL 17", "Postgres"],
            "review_queue_staged": True,
        },
    },
    {
        "id": "conflict-02-api-protocol",
        "category": "cross_speaker_conflict",
        "setup": [
            {"content": "I established gRPC as our inter-service communication protocol.", "occurred_at": "-10d", "speaker": "user"},
            {"content": "Vikas claims we should use GraphQL instead of gRPC.", "occurred_at": "-2d", "speaker": "Vikas"},
        ],
        "query": {"text": "What RPC protocol did I establish?"},
        "expect": {
            "answer_contains_any": ["gRPC"],
            "review_queue_staged": True,
        },
    },
    {
        "id": "conflict-03-launch-date",
        "category": "cross_speaker_conflict",
        "setup": [
            {"content": "Our official public launch date is December 15.", "occurred_at": "-8d", "speaker": "user"},
            {"content": "Product manager Priya thinks we should delay launch to January 20.", "occurred_at": "-2d", "speaker": "Priya"},
        ],
        "query": {"text": "What is our scheduled launch date?"},
        "expect": {
            "answer_contains_any": ["December 15"],
            "review_queue_staged": True,
        },
    },
    {
        "id": "conflict-04-cloud-region",
        "category": "cross_speaker_conflict",
        "setup": [
            {"content": "We deploy our primary cluster in AWS Mumbai ap-south-1.", "occurred_at": "-12d", "speaker": "user"},
            {"content": "DevOps engineer Karthik argued we should host in Singapore ap-southeast-1.", "occurred_at": "-3d", "speaker": "Karthik"},
        ],
        "query": {"text": "Where is our primary cluster deployed?"},
        "expect": {
            "answer_contains_any": ["Mumbai", "ap-south-1"],
            "review_queue_staged": True,
        },
    },
    {
        "id": "conflict-05-language-backend",
        "category": "cross_speaker_conflict",
        "setup": [
            {"content": "I selected Python for Lumen backend services.", "occurred_at": "-14d", "speaker": "user"},
            {"content": "Arjun proposed rewriting the backend in Go for concurrency.", "occurred_at": "-4d", "speaker": "Arjun"},
        ],
        "query": {"text": "What language did I select for the backend?"},
        "expect": {
            "answer_contains_any": ["Python"],
            "review_queue_staged": True,
        },
    },
    {
        "id": "conflict-06-state-management",
        "category": "cross_speaker_conflict",
        "setup": [
            {"content": "I decided to use Zustand for lightweight frontend state management.", "occurred_at": "-9d", "speaker": "user"},
            {"content": "Frontend lead Sneha wants to introduce Redux Toolkit.", "occurred_at": "-2d", "speaker": "Sneha"},
        ],
        "query": {"text": "What state library did I choose?"},
        "expect": {
            "answer_contains_any": ["Zustand"],
            "review_queue_staged": True,
        },
    },
    {
        "id": "conflict-07-container-orchestration",
        "category": "cross_speaker_conflict",
        "setup": [
            {"content": "We run production on Amazon ECS Fargate for simplicity.", "occurred_at": "-15d", "speaker": "user"},
            {"content": "Consultant Dave strongly advised switching to self-managed Kubernetes.", "occurred_at": "-3d", "speaker": "Dave"},
        ],
        "query": {"text": "What container platform do we use in production?"},
        "expect": {
            "answer_contains_any": ["ECS", "Fargate"],
            "review_queue_staged": True,
        },
    },
    {
        "id": "conflict-08-auth-provider",
        "category": "cross_speaker_conflict",
        "setup": [
            {"content": "I implemented Supabase Auth with PKCE flow.", "occurred_at": "-11d", "speaker": "user"},
            {"content": "Security auditor Marcus recommended migrating to Clerk.", "occurred_at": "-1d", "speaker": "Marcus"},
        ],
        "query": {"text": "What authentication provider did I implement?"},
        "expect": {
            "answer_contains_any": ["Supabase"],
            "review_queue_staged": True,
        },
    },
    {
        "id": "conflict-09-license-choice",
        "category": "cross_speaker_conflict",
        "setup": [
            {"content": "We chose Apache 2.0 license for our open core SDK.", "occurred_at": "-20d", "speaker": "user"},
            {"content": "Legal counsel advised switching the SDK to AGPLv3.", "occurred_at": "-4d", "speaker": "Legal"},
        ],
        "query": {"text": "What license did we choose for our SDK?"},
        "expect": {
            "answer_contains_any": ["Apache"],
            "review_queue_staged": True,
        },
    },
    {
        "id": "conflict-10-office-location",
        "category": "cross_speaker_conflict",
        "setup": [
            {"content": "Our main engineering hub is located in Indiranagar Bengaluru.", "occurred_at": "-25d", "speaker": "user"},
            {"content": "Recruiter Tanya suggested opening our primary hub in HSR Layout.", "occurred_at": "-5d", "speaker": "Tanya"},
        ],
        "query": {"text": "Where is our main engineering hub?"},
        "expect": {
            "answer_contains_any": ["Indiranagar"],
            "review_queue_staged": True,
        },
    },
]

# 5. ABSTENTION (15 cases)
ABSTENTION_CASES = [
    {
        "id": "abstain-01-blood-group",
        "category": "abstention",
        "setup": [
            {"content": "I live in Bengaluru and code in Neovim.", "occurred_at": "-3d"},
            {"content": "Lumen uses PostgreSQL 17.", "occurred_at": "-2d"},
        ],
        "query": {"text": "What is my blood group?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-02-mother-maiden-name",
        "category": "abstention",
        "setup": [{"content": "My brother's name is Anshul.", "occurred_at": "-5d"}],
        "query": {"text": "What is my mother's maiden name?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-03-bank-account",
        "category": "abstention",
        "setup": [{"content": "I work at CloudScale as an architect.", "occurred_at": "-4d"}],
        "query": {"text": "What bank do I have my savings account with?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-04-favorite-color",
        "category": "abstention",
        "setup": [{"content": "I drive an electric Hyundai Ioniq 5.", "occurred_at": "-6d"}],
        "query": {"text": "What is my favorite color?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-05-high-school",
        "category": "abstention",
        "setup": [{"content": "My brother studied computer science at IIT Delhi.", "occurred_at": "-8d"}],
        "query": {"text": "Which high school did I attend?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-06-wifi-password",
        "category": "abstention",
        "setup": [{"content": "My laptop is a MacBook Air M3.", "occurred_at": "-10d"}],
        "query": {"text": "What is the password for my home Wi-Fi?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-07-passport-number",
        "category": "abstention",
        "setup": [{"content": "I moved to Indiranagar Bengaluru last month.", "occurred_at": "-12d"}],
        "query": {"text": "What is my passport number?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-08-pet-cat",
        "category": "abstention",
        "setup": [{"content": "My golden retriever is named Barnaby.", "occurred_at": "-7d"}],
        "query": {"text": "What is the name of my pet cat?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-09-salary",
        "category": "abstention",
        "setup": [{"content": "I work as a Staff Systems Architect.", "occurred_at": "-15d"}],
        "query": {"text": "What is my annual base salary?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-10-favorite-movie",
        "category": "abstention",
        "setup": [{"content": "My favorite author is Ted Chiang.", "occurred_at": "-9d"}],
        "query": {"text": "What is my all-time favorite movie?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-11-gym-trainer",
        "category": "abstention",
        "setup": [{"content": "I work out at Gold's Gym.", "occurred_at": "-6d"}],
        "query": {"text": "What is the name of my personal trainer at Gold's Gym?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-12-shoe-brand",
        "category": "abstention",
        "setup": [{"content": "My running shoes are UK size 10.", "occurred_at": "-11d"}],
        "query": {"text": "What brand of running shoes do I wear?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-13-guitar-amplifier",
        "category": "abstention",
        "setup": [{"content": "I play an acoustic Taylor 214ce guitar.", "occurred_at": "-8d"}],
        "query": {"text": "What guitar amplifier do I plug into?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-14-aws-monthly-bill",
        "category": "abstention",
        "setup": [{"content": "Our servers run in AWS ap-south-1 region.", "occurred_at": "-13d"}],
        "query": {"text": "How much was our AWS cloud bill last month?"},
        "expect": {"abstain": True},
    },
    {
        "id": "abstain-15-medical-history",
        "category": "abstention",
        "setup": [{"content": "I drink Sencha green tea and black coffee.", "occurred_at": "-4d"}],
        "query": {"text": "Have I ever had surgery?"},
        "expect": {"abstain": True},
    },
]

# 6. TEMPORAL VALIDITY & TIME TRAVEL (10 cases)
TEMPORAL_CASES = [
    {
        "id": "temporal-01-historical-city",
        "category": "temporal_validity",
        "setup": [
            {"content": "I lived in Mumbai from January 2024 until October 2026.", "occurred_at": "-60d"},
            {"content": "I moved to Bengaluru in November 2026.", "occurred_at": "-10d"},
        ],
        "query": {"text": "Where did I live as of June 2024?", "as_of": "-120d"},
        "expect": {"answer_contains_any": ["Mumbai"]},
    },
    {
        "id": "temporal-02-current-city",
        "category": "temporal_validity",
        "setup": [
            {"content": "I lived in Mumbai from January 2024 until October 2026.", "occurred_at": "-60d"},
            {"content": "I moved to Bengaluru in November 2026.", "occurred_at": "-10d"},
        ],
        "query": {"text": "Where do I live right now?"},
        "expect": {"answer_contains_any": ["Bengaluru"]},
    },
    {
        "id": "temporal-03-old-editor",
        "category": "temporal_validity",
        "setup": [
            {"content": "I used VS Code during 2024.", "occurred_at": "-150d"},
            {"content": "I switched to Neovim in late 2025.", "occurred_at": "-20d"},
        ],
        "query": {"text": "What editor did I use back in mid 2024?", "as_of": "-140d"},
        "expect": {"answer_contains_any": ["VS Code"]},
    },
    {
        "id": "temporal-04-old-database",
        "category": "temporal_validity",
        "setup": [
            {"content": "Lumen was initially prototyped on SQLite.", "occurred_at": "-180d"},
            {"content": "We migrated Lumen to Postgres 17 in October.", "occurred_at": "-15d"},
        ],
        "query": {"text": "What database was Lumen initially prototyped on?"},
        "expect": {"answer_contains_any": ["SQLite"]},
    },
    {
        "id": "temporal-05-recent-database",
        "category": "temporal_validity",
        "setup": [
            {"content": "Lumen was initially prototyped on SQLite.", "occurred_at": "-180d"},
            {"content": "We migrated Lumen to Postgres 17 in October.", "occurred_at": "-15d"},
        ],
        "query": {"text": "What database does Lumen use today?"},
        "expect": {"answer_contains_any": ["Postgres", "PostgreSQL 17"]},
    },
    {
        "id": "temporal-06-contract-validity",
        "category": "temporal_validity",
        "setup": [
            {"content": "Our vendor SLA contract with Datadog is valid until December 2026.", "occurred_at": "-10d"},
        ],
        "query": {"text": "When does our Datadog SLA contract expire?"},
        "expect": {"answer_contains_any": ["December 2026"]},
    },
    {
        "id": "temporal-07-past-company",
        "category": "temporal_validity",
        "setup": [
            {"content": "I worked at Microsoft from 2020 to 2023.", "occurred_at": "-90d"},
            {"content": "I joined CloudScale in 2023.", "occurred_at": "-30d"},
        ],
        "query": {"text": "Where did I work prior to CloudScale?"},
        "expect": {"answer_contains_any": ["Microsoft"]},
    },
    {
        "id": "temporal-08-since-march-anchor",
        "category": "temporal_validity",
        "setup": [
            {"content": "Since March 2026 I have been using Postgres 17 for Lumen.", "occurred_at": "-20d"},
        ],
        "query": {"text": "Since when have I been using Postgres 17 for Lumen?"},
        "expect": {"answer_contains_any": ["March"]},
    },
    {
        "id": "temporal-09-last-week-incident",
        "category": "temporal_validity",
        "setup": [
            {"content": "Last week we suffered a 15-minute outage due to Redis OOM.", "occurred_at": "-7d"},
        ],
        "query": {"text": "What caused the recent 15-minute outage?"},
        "expect": {"answer_contains_any": ["Redis OOM", "Redis"]},
    },
    {
        "id": "temporal-10-future-deadline",
        "category": "temporal_validity",
        "setup": [
            {"content": "Our Q4 roadmap deadline is set for December 31 2026.", "occurred_at": "-14d"},
        ],
        "query": {"text": "When is our Q4 roadmap due?"},
        "expect": {"answer_contains_any": ["December 31"]},
    },
]

# 7. SENSITIVITY & SECRETS (10 cases)
SENSITIVITY_CASES = [
    {
        "id": "secret-01-otp-classification",
        "category": "sensitivity_secrets",
        "setup": [{"content": "My login OTP for verification is 948102.", "occurred_at": "-1d"}],
        "query": {"text": "What was my login OTP?"},
        "expect": {"answer_contains_any": ["948102"]},
    },
    {
        "id": "secret-02-api-key-classified",
        "category": "sensitivity_secrets",
        "setup": [{"content": "My Groq API token is mock_groq_api_token_4RNSYj8ZZZzLORSMSKZE.", "occurred_at": "-2d"}],
        "query": {"text": "What API key did I record for Groq?"},
        "expect": {"answer_contains_any": ["mock_groq_api_token"]},
    },
    {
        "id": "secret-03-openai-secret",
        "category": "sensitivity_secrets",
        "setup": [{"content": "My secret OpenAI key is mock_openai_secret_key_abc1234567890.", "occurred_at": "-3d"}],
        "query": {"text": "What is my OpenAI API key?"},
        "expect": {"answer_contains_any": ["mock_openai_secret_key"]},
    },
    {
        "id": "secret-04-wifi-password-secret",
        "category": "sensitivity_secrets",
        "setup": [{"content": "My secret home router password is PurpleTiger#9284.", "occurred_at": "-4d"}],
        "query": {"text": "What is my home router password?"},
        "expect": {"answer_contains_any": ["PurpleTiger#9284"]},
    },
    {
        "id": "secret-05-database-password",
        "category": "sensitivity_secrets",
        "setup": [{"content": "Production database password is SuperSecretPGPass!99.", "occurred_at": "-5d"}],
        "query": {"text": "What is the production database password?"},
        "expect": {"answer_contains_any": ["SuperSecretPGPass!99"]},
    },
    {
        "id": "secret-06-private-phone-number",
        "category": "sensitivity_secrets",
        "setup": [{"content": "My personal phone number is +91-9876543210.", "occurred_at": "-6d"}],
        "query": {"text": "What is my personal phone number?"},
        "expect": {"answer_contains_any": ["9876543210"]},
    },
    {
        "id": "secret-07-private-email-address",
        "category": "sensitivity_secrets",
        "setup": [{"content": "My private personal email address is nirbhay.personal@gmail.com.", "occurred_at": "-7d"}],
        "query": {"text": "What is my personal email address?"},
        "expect": {"answer_contains_any": ["nirbhay.personal@gmail.com"]},
    },
    {
        "id": "secret-08-bank-routing-number",
        "category": "sensitivity_secrets",
        "setup": [{"content": "Our corporate HDFC bank IFSC routing code is HDFC0001234.", "occurred_at": "-8d"}],
        "query": {"text": "What is our bank IFSC code?"},
        "expect": {"answer_contains_any": ["HDFC0001234"]},
    },
    {
        "id": "secret-09-recovery-seed-phrase",
        "category": "sensitivity_secrets",
        "setup": [{"content": "The backup seed phrase for the cold wallet is apple banana cherry delta echo fox.", "occurred_at": "-9d"}],
        "query": {"text": "What are the words in my backup seed phrase?"},
        "expect": {"answer_contains_any": ["apple banana"]},
    },
    {
        "id": "secret-10-ssh-private-key-path",
        "category": "sensitivity_secrets",
        "setup": [{"content": "My staging deployment SSH private key is located at ~/.ssh/id_ed25519_deploy.", "occurred_at": "-10d"}],
        "query": {"text": "Where is my deploy SSH private key saved?"},
        "expect": {"answer_contains_any": ["id_ed25519_deploy"]},
    },
]

# 8. CODE & WORK LANES (10 cases)
CODE_WORK_CASES = [
    {
        "id": "code-01-repo-url",
        "category": "code_and_work_lanes",
        "setup": [{"content": "Lumen core repository is hosted at github.com/forion/lumen.", "occurred_at": "-15d"}],
        "query": {"text": "What is the git repository URL for Lumen?", "context": "code"},
        "expect": {"answer_contains_any": ["github.com/forion/lumen"]},
    },
    {
        "id": "code-02-test-command",
        "category": "code_and_work_lanes",
        "setup": [{"content": "To run memory engine unit tests, execute pytest packages/memory-engine/tests -q.", "occurred_at": "-10d"}],
        "query": {"text": "How do I run memory engine unit tests?", "context": "code"},
        "expect": {"answer_contains_any": ["packages/memory-engine/tests"]},
    },
    {
        "id": "code-03-docker-compose-port",
        "category": "code_and_work_lanes",
        "setup": [{"content": "PostgreSQL in docker-compose is bound to localhost port 5432.", "occurred_at": "-12d"}],
        "query": {"text": "What port does docker-compose Postgres bind to?", "context": "code"},
        "expect": {"answer_contains_any": ["5432"]},
    },
    {
        "id": "code-04-embedding-dimension",
        "category": "code_and_work_lanes",
        "setup": [{"content": "The nomic-embed-text model generates 768-dimensional vector embeddings.", "occurred_at": "-14d"}],
        "query": {"text": "What is the vector dimension of nomic-embed-text?", "context": "code"},
        "expect": {"answer_contains_any": ["768"]},
    },
    {
        "id": "code-05-circuit-breaker-threshold",
        "category": "code_and_work_lanes",
        "setup": [{"content": "The memory client trips its circuit breaker after 3 consecutive HTTP failures.", "occurred_at": "-8d"}],
        "query": {"text": "How many consecutive failures trip the memory circuit breaker?", "context": "code"},
        "expect": {"answer_contains_any": ["3"]},
    },
    {
        "id": "code-06-architecture-pattern",
        "category": "code_and_work_lanes",
        "setup": [{"content": "Lumen persistence follows hexagonal architecture with adapters in app/adapters/.", "occurred_at": "-16d"}],
        "query": {"text": "What architectural pattern organizes Lumen adapters?", "context": "code"},
        "expect": {"answer_contains_any": ["hexagonal"]},
    },
    {
        "id": "code-07-work-sprint-goal",
        "category": "code_and_work_lanes",
        "setup": [{"content": "Our current sprint goal at CloudScale is optimizing p99 ingestion latency under 200ms.", "occurred_at": "-5d"}],
        "query": {"text": "What is our current sprint objective at work?", "context": "work"},
        "expect": {"answer_contains_any": ["latency", "200ms"]},
    },
    {
        "id": "code-08-redis-db-index",
        "category": "code_and_work_lanes",
        "setup": [{"content": "Lumen Redis queue uses logical database index 0.", "occurred_at": "-11d"}],
        "query": {"text": "What Redis database number does Lumen use?", "context": "code"},
        "expect": {"answer_contains_any": ["0"]},
    },
    {
        "id": "code-09-outbox-table-purpose",
        "category": "code_and_work_lanes",
        "setup": [{"content": "The transactional outbox table emits CDC change events for vault markdown projection.", "occurred_at": "-9d"}],
        "query": {"text": "What downstream projection consumes the outbox table?", "context": "code"},
        "expect": {"answer_contains_any": ["vault", "markdown"]},
    },
    {
        "id": "code-10-luhn-algorithm",
        "category": "code_and_work_lanes",
        "setup": [{"content": "We use Luhn checksum validation in sensitivity.py to detect credit card numbers.", "occurred_at": "-7d"}],
        "query": {"text": "What algorithm validates payment card numbers in our scanner?", "context": "code"},
        "expect": {"answer_contains_any": ["Luhn"]},
    },
]

ALL_CATEGORIES = [
    FACT_CASES,
    SUPERSEDE_CASES,
    REAFFIRMATION_CASES,
    CONFLICT_CASES,
    ABSTENTION_CASES,
    TEMPORAL_CASES,
    SENSITIVITY_CASES,
    CODE_WORK_CASES,
]


def generate_all():
    count = 0
    for group in ALL_CATEGORIES:
        for case in group:
            file_path = CASES_DIR / f"{case['id']}.yaml"
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.dump(case, f, sort_keys=False, allow_unicode=True)
            count += 1
    print(f"Generated {count} evaluation cases in {CASES_DIR}")


if __name__ == "__main__":
    generate_all()
