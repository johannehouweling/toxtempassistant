# CHANGELOG

<!-- version list -->

## v3.31.1 (2026-08-10)

### Bug Fixes

- **export**: Only export sections of the assay's own question set
  ([`eda5d20`](https://github.com/johannehouweling/toxtempassistant/commit/eda5d20c7769964c53ad66bc88e3b6511c7e7269))

### Chores

- **deps**: Bump aiohttp from 3.14.1 to 3.14.3
  ([`dbba0ec`](https://github.com/johannehouweling/toxtempassistant/commit/dbba0ecf51d2b6e2b20c98a847fd62f247c22697))

- **deps**: Bump cryptography from 49.0.0 to 50.0.0
  ([`40c311f`](https://github.com/johannehouweling/toxtempassistant/commit/40c311f20c24c61c02787ccdbcaff13f5336bd04))

- **deps**: Bump langgraph-checkpoint from 4.0.2 to 4.1.1
  ([`ad2c801`](https://github.com/johannehouweling/toxtempassistant/commit/ad2c801100dd8a0abe4fbeeb6021d0473e0ff6aa))

- **deps**: Bump langsmith from 0.8.0 to 0.8.18
  ([`e83826d`](https://github.com/johannehouweling/toxtempassistant/commit/e83826d88f03fe5e7ac9ba476b228ce971fc009e))

- **deps**: Bump nltk from 3.9.4 to 3.10.0
  ([`cfc472d`](https://github.com/johannehouweling/toxtempassistant/commit/cfc472d01b069492488d041bed29d841a886d38b))

- **deps**: Bump pillow from 12.2.0 to 12.3.0
  ([`e10f611`](https://github.com/johannehouweling/toxtempassistant/commit/e10f611e827bcb45208a915eef7e0535e6466f58))

- **deps**: Bump pydantic-settings from 2.14.0 to 2.14.2
  ([`60595e4`](https://github.com/johannehouweling/toxtempassistant/commit/60595e40a7edb66b8c050bd8c939149be1cf05d7))

- **deps**: Bump pypdf from 6.13.3 to 6.14.2
  ([`91c0cf4`](https://github.com/johannehouweling/toxtempassistant/commit/91c0cf456eaae1f9c61deb951e2bd3a772645802))

- **deps**: Bump soupsieve from 2.8.3 to 2.8.4
  ([`ce11916`](https://github.com/johannehouweling/toxtempassistant/commit/ce11916c00c8e746c89555fa64347278e2f7949d))

- **deps-dev**: Bump bleach from 6.3.0 to 6.4.0
  ([`4c34fd5`](https://github.com/johannehouweling/toxtempassistant/commit/4c34fd5994c2f98974516a47b22a0cc27db91ea1))

- **deps-dev**: Bump gitpython from 3.1.50 to 3.1.57
  ([`154175c`](https://github.com/johannehouweling/toxtempassistant/commit/154175c1aec3146e2b87ef68b11bc9ffb9f8b4fe))

- **deps-dev**: Bump jupyterlab from 4.5.7 to 4.5.10
  ([`1e66492`](https://github.com/johannehouweling/toxtempassistant/commit/1e66492d070da7850ace53abc803e2b9cfd84ea6))

- **deps-dev**: Bump mistune from 3.2.1 to 3.3.0
  ([`4eed994`](https://github.com/johannehouweling/toxtempassistant/commit/4eed9946cb65cc5849754a5fa1071132b851987d))


## v3.31.0 (2026-06-29)

### Bug Fixes

- **answer**: Open ToxTemp guidance popover below the trigger; fix attribution
  ([`ecec717`](https://github.com/johannehouweling/toxtempassistant/commit/ecec717b9b62be96a010f3c8c1880a2ea67c353b))

### Features

- Per-question ToxTemp guidance popovers (RISK-HUNT3R)
  ([`d9c3eb7`](https://github.com/johannehouweling/toxtempassistant/commit/d9c3eb77ed6aae1cbbf50f6fb78ab5be7bb98c85))

- **answer**: Update guidance popover to remove arrow and change trigger to hover
  ([`8760ad2`](https://github.com/johannehouweling/toxtempassistant/commit/8760ad2b4efc7e21e2f78e1b6c2f2c27848969d2))


## v3.30.1 (2026-06-28)

### Bug Fixes

- **answer**: Point readiness link to the test-method DB and drop the section divider
  ([`9182f7d`](https://github.com/johannehouweling/toxtempassistant/commit/9182f7df63b2dfaf941030cce4614666e827787e))


## v3.30.0 (2026-06-28)

### Features

- Add riskhunt3r_db_label to various sections for enhanced categorization
  ([`48bad7c`](https://github.com/johannehouweling/toxtempassistant/commit/48bad7ceaef5bb23c38a5b736390451d84fbe4a8))

- Per-level NAM-readiness progress breakdown on the answer page
  ([`ff3ae0f`](https://github.com/johannehouweling/toxtempassistant/commit/ff3ae0fc37d88df8ffc8464d0c5487902653b189))


## v3.29.1 (2026-06-28)

### Bug Fixes

- Only un-accept an answer on a genuine edit, not on click-to-edit open
  ([`f3836d8`](https://github.com/johannehouweling/toxtempassistant/commit/f3836d8799a91663b4255da7c5c0d56cb02e3751))

- Spotlight the visible answer preview in onboarding tour
  ([`b49cc3a`](https://github.com/johannehouweling/toxtempassistant/commit/b49cc3af9ebffa371120859ddac0a86578a9bdcd))


## v3.29.0 (2026-06-28)

### Bug Fixes

- **gold_standard**: Update README and audit.py to clarify excluded assay IDs; modify
  status_table.py to group by assay_id
  ([`0c1ef1a`](https://github.com/johannehouweling/toxtempassistant/commit/0c1ef1add223702393d1065e2d07993b8945c4a1))

### Features

- **calibrate_relevancy**: Add script to calibrate response-relevancy judge against reference judge
  ([`03b8b1a`](https://github.com/johannehouweling/toxtempassistant/commit/03b8b1add8ce0e9d98e6be130a586b6dc3e9d851))

- **docs**: Update README to clarify handling of known non-gold assays in analysis
  ([`46a7846`](https://github.com/johannehouweling/toxtempassistant/commit/46a7846a16c6f59ce03842693f7906dc81d453f4))

- **docs**: Update README to include new benchmark files and clarify data organization
  ([`a97cef8`](https://github.com/johannehouweling/toxtempassistant/commit/a97cef8587a811b618e880acaf9e067933ab964c))

- **edit_report**: Add edit analysis script to summarize scientist edits on accepted answers
  ([`e4b75a8`](https://github.com/johannehouweling/toxtempassistant/commit/e4b75a872c53871e8e7dea6a9d49a24ca338c3a8))

- **gold_standard**: Exclude known non-gold assays from collection process
  ([`4de6a29`](https://github.com/johannehouweling/toxtempassistant/commit/4de6a29b3462deaedf1d645ebb475138f856cfdf))

- **relevancy**: Implement RAGAS-based response relevancy metric and integrate into tier 3
  evaluation
  ([`95a3ab6`](https://github.com/johannehouweling/toxtempassistant/commit/95a3ab65e4fd3d6c92677b85d2f3ccbecca24792))

### Refactoring

- **gold_standard**: Reorganize output directories and update file paths for analysis and plotting
  ([`f0b49ce`](https://github.com/johannehouweling/toxtempassistant/commit/f0b49ce0dba9aa1cd1c9277d7dd2b924498bb537))


## v3.28.1 (2026-06-28)

### Bug Fixes

- **answers**: Keep parenthesised filenames intact in citation footnotes
  ([`91b0dd1`](https://github.com/johannehouweling/toxtempassistant/commit/91b0dd188dbe4462d0e84266173bb1c2454ace39))


## v3.28.0 (2026-06-28)

### Features

- Render answers as click-to-edit markdown with citation footnotes
  ([`a15d995`](https://github.com/johannehouweling/toxtempassistant/commit/a15d995d1f42c791ee2dd38f09a127f311e0703d))

### Refactoring

- Drop redundant markdown modal button; fix preview trailing margin
  ([`dbd0ed0`](https://github.com/johannehouweling/toxtempassistant/commit/dbd0ed013284e891e72b31e84fc8395abda64f1f))


## v3.27.0 (2026-06-28)

### Features

- **demo**: Harden seeding and keep the demo visible until the user deletes it
  ([`ffd80ab`](https://github.com/johannehouweling/toxtempassistant/commit/ffd80ab8e3d7b168bff4d68aaae3e56b944b5cc3))


## v3.26.0 (2026-06-28)

### Features

- **admin**: Exclude demo assays from the main Assays list
  ([`d306a2b`](https://github.com/johannehouweling/toxtempassistant/commit/d306a2b2c06e6da3803e045949563e6247d227a1))


## v3.25.0 (2026-06-28)

### Features

- **admin**: Surface demo assays in their own admin section
  ([`4c9c038`](https://github.com/johannehouweling/toxtempassistant/commit/4c9c038695831a282d0c75d413e35a2f5b01baea))


## v3.24.1 (2026-06-28)

### Bug Fixes

- **demo**: Seed newest flagged template when multiple are marked
  ([`a5f68b6`](https://github.com/johannehouweling/toxtempassistant/commit/a5f68b6913884898f2de81fca2515aea81d6997b))

### Chores

- **deps**: Bump pyjwt from 2.12.1 to 2.13.0
  ([`86dcbbb`](https://github.com/johannehouweling/toxtempassistant/commit/86dcbbbfe91e6a041d52bbbeb96fdb151732b6e3))

- **deps**: Bump pypdf from 6.12.0 to 6.13.3
  ([`720475c`](https://github.com/johannehouweling/toxtempassistant/commit/720475c6690814ac784b0176369d991a24cff95c))

- **deps-dev**: Bump jupyter-server from 2.18.0 to 2.20.0
  ([`aeedd50`](https://github.com/johannehouweling/toxtempassistant/commit/aeedd50074c67bdbf4b675319c8fc771ec56fa58))


## v3.24.0 (2026-06-16)

### Features

- **gold_standard**: Add --no-cosine prod read + local enrich_gold_cosines
  ([`3c9c7dd`](https://github.com/johannehouweling/ToxTempAssistant/commit/3c9c7dda899b7bed7abf8376f06cc15543af175b))


## v3.23.0 (2026-06-16)

### Chores

- **deps**: Bump aiohttp from 3.14.0 to 3.14.1
  ([`45e42f0`](https://github.com/johannehouweling/ToxTempAssistant/commit/45e42f056a7b2192f91989d8eb43731c5fff9207))

- **deps**: Bump cryptography from 47.0.0 to 48.0.1
  ([`875c59c`](https://github.com/johannehouweling/ToxTempAssistant/commit/875c59c570be7e8ec46a4660ba2f5c762a9e4de5))

- **deps**: Bump pypdf from 6.10.2 to 6.12.0
  ([`3181422`](https://github.com/johannehouweling/ToxTempAssistant/commit/3181422b486d874b042e0ba9044738b977be8845))

- **deps-dev**: Bump tornado from 6.5.5 to 6.5.7
  ([`fedb40f`](https://github.com/johannehouweling/ToxTempAssistant/commit/fedb40f94131c63eb58280df6ea7a45daf461f33))

- **diag**: Add --classify mode to dump post-cutoff user=None draft snapshots
  ([`d512d0d`](https://github.com/johannehouweling/ToxTempAssistant/commit/d512d0dc0e3efed62df6e52ae2ea50609c6d26f4))

- **diag**: Add --scan mode to dump_answer_history (count user=None draft snapshots by era)
  ([`55b062a`](https://github.com/johannehouweling/ToxTempAssistant/commit/55b062a71a3397734a8d16e10cd1cd23334ca93c))

- **diag**: Add read-only dump_answer_history diagnostic command
  ([`f715f6c`](https://github.com/johannehouweling/ToxTempAssistant/commit/f715f6cb9c3cd630602e6751ea0e1d8079a41520))

### Code Style

- **diag**: Fix line lengths in dump_answer_history --scan
  ([`d66e5ce`](https://github.com/johannehouweling/ToxTempAssistant/commit/d66e5ce79dab7df067de9a112cfa7ee1a8f77c7d))

- **diag**: Trim line length
  ([`d018479`](https://github.com/johannehouweling/ToxTempAssistant/commit/d0184790a5bba925f8a33953302b8d0080c58a9a))

### Documentation

- **gold_standard**: Enhance README with detailed run instructions for local and production
  environments
  ([`c9a9c79`](https://github.com/johannehouweling/ToxTempAssistant/commit/c9a9c79aabef3d93de48ef08c89dd53464576055))

### Features

- **gold_standard**: Baseline→accepted edit delta for ALL accepted answers
  ([`c72fbc1`](https://github.com/johannehouweling/ToxTempAssistant/commit/c72fbc18cd230e37619e78375a698638395e34ce))


## v3.22.0 (2026-06-16)

### Features

- **gold_standard**: Implement gold-standard extraction and edit-typing analysis
  ([`4acd16e`](https://github.com/johannehouweling/ToxTempAssistant/commit/4acd16e3664dd65a4f7f20d148e9e704767141f8))

### Refactoring

- **audit**: Improve comments and enhance cache persistence in embedding phase
  ([`b06aa42`](https://github.com/johannehouweling/ToxTempAssistant/commit/b06aa42d7dc38c9750aaf5e66473044f8ede44c6))


## v3.21.0 (2026-06-16)

### Chores

- **deps**: Bump aiohttp from 3.13.5 to 3.14.0
  ([`037b0be`](https://github.com/johannehouweling/ToxTempAssistant/commit/037b0be481974707e927f278c1810a8d6b08e0c1))


## v3.20.0 (2026-06-02)

### Chores

- **deps**: Bump idna from 3.13 to 3.15
  ([`7e6ae7b`](https://github.com/johannehouweling/ToxTempAssistant/commit/7e6ae7b39f03c13a4e5f9d8ab6e9b51f52d38350))

- **deps**: Bump langchain-classic from 1.0.4 to 1.0.7
  ([`0a92357`](https://github.com/johannehouweling/ToxTempAssistant/commit/0a923579e726d67407a5c06589501d93f7de3718))

- **deps**: Bump langsmith from 0.7.36 to 0.8.0
  ([`f25f0ab`](https://github.com/johannehouweling/ToxTempAssistant/commit/f25f0ab2dc59142f9c2f5f440cd5b08e90239dce))

- **deps**: Bump urllib3 from 2.6.3 to 2.7.0
  ([`28c2adb`](https://github.com/johannehouweling/ToxTempAssistant/commit/28c2adbe8217d86e58d42fc71710e608e05c9eba))

### Features

- **azure**: Add temperature handling in model configuration and display
  ([`218883c`](https://github.com/johannehouweling/ToxTempAssistant/commit/218883c0436a3cfd0326af144031ac4cf271e6ee))


## v3.19.6 (2026-05-12)

### Bug Fixes

- Add mw-100 class to cost popover
  ([`191b182`](https://github.com/johannehouweling/ToxTempAssistant/commit/191b1824258fe8e2d5c385d19eb90e0c9dae05cf))


## v3.19.5 (2026-05-10)

### Bug Fixes

- Keep cost breakdown table within popover bounds
  ([`aeb98fa`](https://github.com/johannehouweling/ToxTempAssistant/commit/aeb98faa660792ed6e14ff92e9968341ac3bbbdd))

- Use bootstrap-only popover overflow handling and multi-row test
  ([`1a0ccc3`](https://github.com/johannehouweling/ToxTempAssistant/commit/1a0ccc33c14a113d2c2353815f6b0b338f080fbd))


## v3.19.4 (2026-05-10)

### Bug Fixes

- Extend Bootstrap allowList so cost breakdown popover renders table HTML
  ([`e411715`](https://github.com/johannehouweling/ToxTempAssistant/commit/e411715b7166641b602d72765c7f5914651f5112))

- **ci**: Add cronlogs timestamp
  ([`700a134`](https://github.com/johannehouweling/ToxTempAssistant/commit/700a1341317236799babc1d9f0a1a18f3d00292e))

### Chores

- **deps**: Bump langchain-core from 1.3.2 to 1.3.3
  ([`f07dbba`](https://github.com/johannehouweling/ToxTempAssistant/commit/f07dbba9fc4ef72a4e1bacd7a1a7600b818ad564))


## v3.19.3 (2026-05-09)

### Bug Fixes

- Keep workspace icon inline in overview investigation column
  ([`c033ee1`](https://github.com/johannehouweling/ToxTempAssistant/commit/c033ee125dd97ef943c75385b1dfc01afc12bdc9))

- Make investigation share toggle semantic and whitespace-safe
  ([`dc2b391`](https://github.com/johannehouweling/ToxTempAssistant/commit/dc2b3912806760581aec0c49d0b477a5ecdcb887))

### Chores

- **deps**: Bump django from 6.0.4 to 6.0.5
  ([`404506c`](https://github.com/johannehouweling/ToxTempAssistant/commit/404506ce59a73d16fce111557fc9642920b2b471))

- **deps-dev**: Bump gitpython from 3.1.49 to 3.1.50
  ([`2ed6c78`](https://github.com/johannehouweling/ToxTempAssistant/commit/2ed6c7861918cfffcc39c94767b776eef565aba5))


## v3.19.2 (2026-05-09)

### Bug Fixes

- **ci**: Backup now via TCP
  ([`d12e3ad`](https://github.com/johannehouweling/ToxTempAssistant/commit/d12e3ade64702d99c6e9a5e4afb9af48428451c9))


## v3.19.1 (2026-05-09)

### Bug Fixes

- Improve signup ROR lookup matching
  ([`690afbe`](https://github.com/johannehouweling/ToxTempAssistant/commit/690afbe8403a250217ecddbe0bcb6a1bbdbfde6e))

- **ci**: Healthcheck is host toxtempasassistant
  ([`e40cba0`](https://github.com/johannehouweling/ToxTempAssistant/commit/e40cba008cc93a1c6a49bbe19ed7c51be1486bdb))

### Refactoring

- Sync signup ROR lookup thresholds
  ([`b5ad45d`](https://github.com/johannehouweling/ToxTempAssistant/commit/b5ad45d93fc17f5da073770eff6b1cfcc52fa2f2))

### Testing

- Use realistic ROR signup payload mocks
  ([`ad01ff7`](https://github.com/johannehouweling/ToxTempAssistant/commit/ad01ff78798796c1bc171e80095d57ce56d50208))


## v3.19.0 (2026-05-08)

### Bug Fixes

- Address all PR review comments on time tracking
  ([`be56732`](https://github.com/johannehouweling/ToxTempAssistant/commit/be56732e5c4e065a5f6ec25234eef99240498bae))

- Address code review feedback (XSS-safe id parsing, interval cleanup, naming)
  ([`ba81b92`](https://github.com/johannehouweling/ToxTempAssistant/commit/ba81b927c1f5cb20f91a408e7020835b15a3ff2b))

- Label time_spent_seconds as personal contribution in feedback modal
  ([`9d8e615`](https://github.com/johannehouweling/ToxTempAssistant/commit/9d8e615d040fc20fe4f18e6ddd585ec9459f8b55))

- Scope localStorage time-tracking key per user to support workspace collaboration
  ([`9bd8913`](https://github.com/johannehouweling/ToxTempAssistant/commit/9bd891394bc93489dafca2f756928f26dc76df69))

- **ci**: Healthcheck on deploy on swarm now in docker not in docker action
  ([`b053dab`](https://github.com/johannehouweling/ToxTempAssistant/commit/b053dabf9606af24635c5afd55efec5aa513d89a))

### Features

- Add automated active-time tracking for assay completion
  ([`cf89c96`](https://github.com/johannehouweling/ToxTempAssistant/commit/cf89c969430e588252e2b7876eb0fe7019d97400))

- Auto-capture completion time when all answers accepted; decouple from feedback form
  ([`aa61d96`](https://github.com/johannehouweling/ToxTempAssistant/commit/aa61d96e3e8ccccba25550e8558be68963d4f61c))

- Hybrid server-side time aggregation for workspace collaboration
  ([`d069422`](https://github.com/johannehouweling/ToxTempAssistant/commit/d069422bdd2c5e2ed3e4e944e0c08feb4cdbfb49))


## v3.18.0 (2026-05-08)

### Bug Fixes

- Add contributor authors to export metadata
  ([`162b780`](https://github.com/johannehouweling/ToxTempAssistant/commit/162b780ba2742c7bd7eb9076b38b6d9f32be6db5))

- Align yaml export author metadata
  ([`664ee54`](https://github.com/johannehouweling/ToxTempAssistant/commit/664ee548db396abb4ccaf4585b8dc87c31d4647b))

- Flatten yaml export author metadata
  ([`e1da9c8`](https://github.com/johannehouweling/ToxTempAssistant/commit/e1da9c83c4d102e20606d84640ca6c754d91d47d))

- Respect investigation ownership in export authors
  ([`8dca191`](https://github.com/johannehouweling/ToxTempAssistant/commit/8dca191d3183b1ee78b6a1127c5db60cdef1daff))

### Chores

- Address export metadata review feedback
  ([`5890665`](https://github.com/johannehouweling/ToxTempAssistant/commit/5890665743e24aebec1d4126bc0e0f9bfcd727e1))

- Tighten export author metadata lookup
  ([`3d573d6`](https://github.com/johannehouweling/ToxTempAssistant/commit/3d573d6620c5d0e977d93467e6ab32711b02b7e9))

### Documentation

- Clarify export author metadata docstring
  ([`0c45c89`](https://github.com/johannehouweling/ToxTempAssistant/commit/0c45c89349f4d936871dfdfed0cc684472beec73))

- Clarify exported author metadata helpers
  ([`8ef4531`](https://github.com/johannehouweling/ToxTempAssistant/commit/8ef4531f287944647aabf10ccdeb1b84c6efb86e))

### Features

- Enrich exported author metadata
  ([`cb1a699`](https://github.com/johannehouweling/ToxTempAssistant/commit/cb1a6994a7836f164b603e8f7ba77942a945e86c))

- Export corresponding author identity metadata
  ([`fbde00b`](https://github.com/johannehouweling/ToxTempAssistant/commit/fbde00b8e5aede818b7c0da3ff18728bc008db6b))

### Refactoring

- Simplify export metadata typing
  ([`0d98cce`](https://github.com/johannehouweling/ToxTempAssistant/commit/0d98ccecc6c02b5e21d46e8b04b758836a1b83d9))

### Testing

- Tidy export metadata assertion
  ([`81248e3`](https://github.com/johannehouweling/ToxTempAssistant/commit/81248e3b0218132db58133ef6b9cece6e94383f4))

- Use valid question set labels in export metadata tests
  ([`64da1f1`](https://github.com/johannehouweling/ToxTempAssistant/commit/64da1f18f248d7010653867e332fa28a8e4aae07))


## v3.17.6 (2026-05-08)

### Bug Fixes

- Harden ROR advanced query input handling
  ([`dda8876`](https://github.com/johannehouweling/ToxTempAssistant/commit/dda8876b20d1b1db415e50d5079d07351908c3e3))

- Improve ROR lookup with advanced and domain-first queries
  ([`ee6d7f5`](https://github.com/johannehouweling/ToxTempAssistant/commit/ee6d7f5b87bdb584818a51423727aa9b29f922fd))

- Keep email domain literal in ROR lookup
  ([`8b405fb`](https://github.com/johannehouweling/ToxTempAssistant/commit/8b405fbb963007c764f8cecff001e681b228398a))

- Tighten domain parsing in ROR advanced lookup flow
  ([`80f975c`](https://github.com/johannehouweling/ToxTempAssistant/commit/80f975c1263795b98eafc943a85d60e11f2b6491))

### Chores

- Polish ROR lookup hardening details
  ([`fbe9c08`](https://github.com/johannehouweling/ToxTempAssistant/commit/fbe9c088b0072a8d974a71a7a0ad49fb16cdb217))

### Documentation

- Clarify RFC-style domain validation intent
  ([`1ae6b1c`](https://github.com/johannehouweling/ToxTempAssistant/commit/1ae6b1cbfe9112505c02bd331ec651c0a52d1b1b))

### Testing

- Cover ROR email-domain parsing and dedupe behavior
  ([`0d68cb5`](https://github.com/johannehouweling/ToxTempAssistant/commit/0d68cb5096239e099239ed0c1c565bcdd25e5e22))


## v3.17.5 (2026-05-08)

### Bug Fixes

- **ror**: Add v2 endpoint for ror lookup
  ([`ee11947`](https://github.com/johannehouweling/ToxTempAssistant/commit/ee1194726ff4e96b0ac87d1d9767204a35d11a0f))


## v3.17.4 (2026-05-08)

### Bug Fixes

- Harden deploy readiness helper
  ([`2952587`](https://github.com/johannehouweling/ToxTempAssistant/commit/29525875d256155e094b985f02f9f3f19d7b4c75))

- Read deploy probe timing from env file
  ([`72153b4`](https://github.com/johannehouweling/ToxTempAssistant/commit/72153b461ed84e214e26cfc0b19c60f1820d669a))

- Read deploy readiness settings from service env
  ([`0aabd5d`](https://github.com/johannehouweling/ToxTempAssistant/commit/0aabd5d45f90276219163db225ee515fa84b3025))

- Streamline deploy readiness helper
  ([`5dc2388`](https://github.com/johannehouweling/ToxTempAssistant/commit/5dc2388977f43edec4dda6cd7da6df63acd7da9d))

- Tighten deploy readiness probes
  ([`f9623f7`](https://github.com/johannehouweling/ToxTempAssistant/commit/f9623f76f82c3ed952919d27856b5e55e6646f80))

- Wait for deploy target to serve http
  ([`f0acf61`](https://github.com/johannehouweling/ToxTempAssistant/commit/f0acf61805d1131d9ce1182fb3e8cdfb1b014ef1))

### Chores

- Improve deploy probe diagnostics
  ([`03385ff`](https://github.com/johannehouweling/ToxTempAssistant/commit/03385ff42cc99e6c86732ee1e2c30db77ea556fa))

- Narrow deploy env parsing
  ([`8ea3f37`](https://github.com/johannehouweling/ToxTempAssistant/commit/8ea3f3733e4410f62567baec15332a71d57394ba))

- Refine deploy readiness probes
  ([`e39690f`](https://github.com/johannehouweling/ToxTempAssistant/commit/e39690f23590f3bb321e90700ef845e6521a17e0))

- Sanitize deploy env parsing
  ([`1c2c5a0`](https://github.com/johannehouweling/ToxTempAssistant/commit/1c2c5a01cb553862f99a11a4919f920c3738aeb0))

- Simplify deploy probe piping
  ([`b08d4d5`](https://github.com/johannehouweling/ToxTempAssistant/commit/b08d4d5ae06f9ba4645ede2f886fb283d7013878))

- Tighten deploy status matching
  ([`054ba2a`](https://github.com/johannehouweling/ToxTempAssistant/commit/054ba2a1e95309ea2df7756a447ea837dc07893b))


## v3.17.3 (2026-05-08)

### Bug Fixes

- Support nested ROR organization lookup payloads
  ([`91b7320`](https://github.com/johannehouweling/ToxTempAssistant/commit/91b73208684308bacc1aae65243e7ce04355ecf5))


## v3.17.2 (2026-05-08)

### Bug Fixes

- **tables**: Remove leaking text in export menu
  ([`61e952c`](https://github.com/johannehouweling/ToxTempAssistant/commit/61e952c8e76ff25ee62e701fdcd8f750e441c4aa))


## v3.17.1 (2026-05-08)

### Bug Fixes

- **export**: Exports from overview page now go through feedback_export() the same wat the editing
  pages does.
  ([`365ee88`](https://github.com/johannehouweling/ToxTempAssistant/commit/365ee88e617f41f59e90f08afdb486d76aa05e00))

- **export**: Remove dead md block
  ([`11dd92f`](https://github.com/johannehouweling/ToxTempAssistant/commit/11dd92fcf5f324d22e7258611bc51735fd669c54))

- **export**: Report actual LLMs used and resolve broken metadata fields
  ([`56067f2`](https://github.com/johannehouweling/ToxTempAssistant/commit/56067f28be3ea831adef5ec0c42eb1b282b570b1))

- **ui**: Rename GPT update wording to LLM update
  ([`64c50e4`](https://github.com/johannehouweling/ToxTempAssistant/commit/64c50e44174250471ea4875fd7e25d52d23f9454))


## v3.17.0 (2026-05-08)

### Bug Fixes

- Harden ror lookup and affiliation export formatting
  ([`034719a`](https://github.com/johannehouweling/ToxTempAssistant/commit/034719aa949c6f6a9c962ecb51f2275162f62c35))

- Move ror constants to config and use email author fallback
  ([`ab4cef0`](https://github.com/johannehouweling/ToxTempAssistant/commit/ab4cef0b561c41c11766fe2acc9db3c1098b233d))

### Documentation

- **agents**: Update conventions for coding agents.
  ([`4e6a991`](https://github.com/johannehouweling/ToxTempAssistant/commit/4e6a991ee2716394c4dda7e544d4dccb585e102d))

### Features

- Add organization lookup and affiliation export metadata
  ([`174804f`](https://github.com/johannehouweling/ToxTempAssistant/commit/174804fd79263a0cb46c82b6f6daaec4ea177a8a))


## v3.16.7 (2026-05-07)

### Bug Fixes

- **ci**: Remove BACKUP_ROOT from .env file, now always stores to /work/backups inside docker (which
  is mounted to a names location via docker comp
  ([`0decf6b`](https://github.com/johannehouweling/ToxTempAssistant/commit/0decf6b7446a2c72336b40fa5e56ca8b750cb562))

### Chores

- **deps-dev**: Bump gitpython from 3.1.47 to 3.1.49
  ([`7ed95d4`](https://github.com/johannehouweling/ToxTempAssistant/commit/7ed95d4db003615f349b2e1eabd9fbce1ff66d2c))

- **deps-dev**: Bump jupyter-server from 2.17.0 to 2.18.0
  ([`7500235`](https://github.com/johannehouweling/ToxTempAssistant/commit/7500235c3ed7dac1aaee5c111be4b479956993d9))

- **deps-dev**: Bump mistune from 3.2.0 to 3.2.1
  ([`5f489e8`](https://github.com/johannehouweling/ToxTempAssistant/commit/5f489e8c33609f19777b2b75657a1bbe6f712348))

- **deps-dev**: Bump notebook from 7.5.5 to 7.5.6
  ([`42d7b83`](https://github.com/johannehouweling/ToxTempAssistant/commit/42d7b83b56c63fff20097af19e717578f01925f0))


## v3.16.6 (2026-05-06)

### Bug Fixes

- Restore original help_text for assay description field
  ([`6807a35`](https://github.com/johannehouweling/ToxTempAssistant/commit/6807a35b413eb999d512b6a283215e71d2178f7f))

- Show assay description instructions as help_text on all devices
  ([`d2d0b0a`](https://github.com/johannehouweling/ToxTempAssistant/commit/d2d0b0acf30e3c886aed5e9cdf8dcf7543a66e89))

### Documentation

- **ci**: Swarm deploy docs
  ([`d232717`](https://github.com/johannehouweling/ToxTempAssistant/commit/d2327176c910532a320da81beed0099a3410367e))


## v3.16.5 (2026-05-04)

### Bug Fixes

- **ci**: Adjust backup location on swarm
  ([`c936ec5`](https://github.com/johannehouweling/ToxTempAssistant/commit/c936ec50bf4ff12721f0eb7f41595ba3b878dc87))


## v3.16.4 (2026-05-04)

### Bug Fixes

- **ci**: Ignore github actions and documentation in CI cycle
  ([`1446165`](https://github.com/johannehouweling/ToxTempAssistant/commit/14461652b08816cbceb805656d578d86d60a26b2))

- **django**: Replace nginx dep on legcay with whitenoise container internal
  ([`90481b5`](https://github.com/johannehouweling/ToxTempAssistant/commit/90481b57cb2a4472c3e2d179e7c3b677a66f7892))


## v3.16.3 (2026-05-04)

### Bug Fixes

- **ci**: Also strip top-level 'name' (and a few other modern-only fields) before stack deploy
  ([`240ca2f`](https://github.com/johannehouweling/ToxTempAssistant/commit/240ca2f2d1a9bbf6926fba7a7ca573418d0babfe))


## v3.16.2 (2026-05-04)

### Bug Fixes

- **ci**: Strip profiles + reset depends_on for stack deploy
  ([`2d97b50`](https://github.com/johannehouweling/ToxTempAssistant/commit/2d97b5076b12dae5672ef41fdf6f8e8868ab0dc9))


## v3.16.1 (2026-05-04)

### Bug Fixes

- **ci**: Save one CI run and drop depends_on
  ([`26d49e0`](https://github.com/johannehouweling/ToxTempAssistant/commit/26d49e007bdc6f15cb1c96f64913263126010f21))


## v3.16.0 (2026-05-04)

### Bug Fixes

- **ci**: Small tweaks to docker setup
  ([`4b924f4`](https://github.com/johannehouweling/ToxTempAssistant/commit/4b924f498d2a0791db28baf5dfbc97169df15da7))

### Features

- **ci**: Add Swarm overlay + parallel deploy-swarm job
  ([`f2e39b8`](https://github.com/johannehouweling/ToxTempAssistant/commit/f2e39b840430203a8f999e5112b9d29a1a6c63ad))


## v3.15.0 (2026-05-03)

### Features

- **ci**: Bake minio init.sh into a custom ghcr.io image
  ([`079c1b6`](https://github.com/johannehouweling/ToxTempAssistant/commit/079c1b65702e3305b0dbc6273d1ecbcda2d1ac90))


## v3.14.2 (2026-05-03)

### Bug Fixes

- **ci**: No --vsc-release
  ([`648bb8a`](https://github.com/johannehouweling/ToxTempAssistant/commit/648bb8a7861c57a78fb686dc28e45f78c380602d))


## v3.14.1 (2026-05-03)

### Bug Fixes

- **ci**: Trigger release after malformed message
  ([`16f5420`](https://github.com/johannehouweling/ToxTempAssistant/commit/16f54208a3205a66bb9c94a617d79c061c120765))


## v3.14.0 (2026-05-03)

### Features

- **ci**: Change backup image and store backup image as well
  ([`a5fc9e5`](https://github.com/johannehouweling/ToxTempAssistant/commit/a5fc9e5f374afc9b9acffaea74671eda0b89c3b5))


## v3.13.0 (2026-05-03)

### Bug Fixes

- **ci**: Add write rights for gha cache
  ([`ccae47c`](https://github.com/johannehouweling/ToxTempAssistant/commit/ccae47ced01cc7f373fb90fc630883e5465c2ece))

### Documentation

- Revise security policy for clarity and updates
  ([`9bad503`](https://github.com/johannehouweling/ToxTempAssistant/commit/9bad50341f4397e040662fd811036377cbee938d))

### Features

- **ci**: Push to ghcr
  ([`0d84d39`](https://github.com/johannehouweling/ToxTempAssistant/commit/0d84d39dc13a1e849b04f7243c7ea0aa4c1919c6))


## v3.12.1 (2026-05-02)

### Bug Fixes

- Normalize token tag names to lowercase in configuration and related code
  ([`66c13e4`](https://github.com/johannehouweling/ToxTempAssistant/commit/66c13e40553d7cc7b017c912e4fb6c831e027add))


## v3.12.0 (2026-05-02)

### Bug Fixes

- Disable Forgot password link until email setup is complete
  ([#140](https://github.com/johannehouweling/ToxTempAssistant/pull/140),
  [`758de81`](https://github.com/johannehouweling/ToxTempAssistant/commit/758de8138742528b58bdfbde08ef6d40f8a6f0c0))

- Update test to reference Config._pw_reset_max_stored after constant move
  ([`3cbdabc`](https://github.com/johannehouweling/ToxTempAssistant/commit/3cbdabc2295b97fc4de26ed297942d8f8b7cd2de))

- Use toxtempass/ prefix for password reset template names
  ([`6d20dab`](https://github.com/johannehouweling/ToxTempAssistant/commit/6d20dabd8ea06068ac347fe7ad73b6ecff60a6b7))

### Features

- Implement password reset with rate limiting spam protection
  ([`888ab39`](https://github.com/johannehouweling/ToxTempAssistant/commit/888ab39e02d949f06fbd462cfecc1205c0173447))

### Refactoring

- Mark _pw_reset_max_stored as Final[int] for consistency
  ([`98ad553`](https://github.com/johannehouweling/ToxTempAssistant/commit/98ad553ac947a5d0e41d86d8637513456e457ffa))

- Move pw reset rate-limit constants from utilities.py to Config
  ([`07b29ef`](https://github.com/johannehouweling/ToxTempAssistant/commit/07b29efbeb8faad7e73880213b73585fb7ea7f6b))


## v3.11.0 (2026-05-02)

### Bug Fixes

- Address code review feedback on cost tracking feature
  ([`4fdbcb5`](https://github.com/johannehouweling/ToxTempAssistant/commit/4fdbcb550c9febb29751583252399dc96fea327d))

- Rename cost tag keys to use capital M (1Mtoken) for million
  ([`e94414e`](https://github.com/johannehouweling/ToxTempAssistant/commit/e94414e717889044741782f5cb3f2af860623304))

- Update generate_answer return type annotation and fix N+1 query in render_cost
  ([`8be5be5`](https://github.com/johannehouweling/ToxTempAssistant/commit/8be5be5f53250d39c102c8798e98148fa09ce521))

- Use EUR currency for cost tracking, move cost column after Answers Accepted
  ([`d6da458`](https://github.com/johannehouweling/ToxTempAssistant/commit/d6da458c25537d308e6e419755efa2227e049461))

### Chores

- **deps-dev**: Bump jupyterlab from 4.5.6 to 4.5.7
  ([`3b82d27`](https://github.com/johannehouweling/ToxTempAssistant/commit/3b82d27b7fb28af2bbc56b695d030212f93c89fe))

### Documentation

- Add Backup architecture section to README
  ([`4b8df2a`](https://github.com/johannehouweling/ToxTempAssistant/commit/4b8df2a0ad6f3f0c9c9d23054de9a42b412c8208))

- Fix RETENTION_DAYS refs and timestamp timezone in backup section
  ([`04e7d4f`](https://github.com/johannehouweling/ToxTempAssistant/commit/04e7d4fdae2d4ed7b0192879cf3110fccd6b1b94))

### Features

- Add cost-unit tag, store in AssayCost and use in cost display
  ([`a836407`](https://github.com/johannehouweling/ToxTempAssistant/commit/a836407949d13a7052c151236d7c1061567bb1da))

- Add LLM cost tracking per assay draft
  ([`2e7a753`](https://github.com/johannehouweling/ToxTempAssistant/commit/2e7a753d0143dacfe303e1111aa6fd7a4afe11a2))


## v3.10.0 (2026-05-01)

### Bug Fixes

- Dispatch change events with bubbles:true so document listener receives them
  ([`abd44ff`](https://github.com/johannehouweling/ToxTempAssistant/commit/abd44ff58e4d6b2e46d48da720f520e04f1c0ec5))

### Chores

- Change DOI link to reference the paper
  ([`1f61e42`](https://github.com/johannehouweling/ToxTempAssistant/commit/1f61e42307ff3f994e48c6e0fb1b353ca253fda5))

- Update referecne from zenodo to Toxtempassistant paper
  ([`eedd15f`](https://github.com/johannehouweling/ToxTempAssistant/commit/eedd15f5aaa2a2d19bf7b68e331f61a22b615743))

### Features

- Add Mark all open for LLM Update toggle to Options dropdown
  ([`8d57416`](https://github.com/johannehouweling/ToxTempAssistant/commit/8d57416146d8fd8496e5b33a7fcb670af2c9a7d5))


## v3.9.0 (2026-05-01)

### Bug Fixes

- Add explicit fontspec and export_type docstring to metadata yaml helper
  ([`e065fd8`](https://github.com/johannehouweling/ToxTempAssistant/commit/e065fd8cd629e6acafc204cdd51dea14bfd5c181))

- Address reviewer feedback on workspace.py
  ([`86f8ac4`](https://github.com/johannehouweling/ToxTempAssistant/commit/86f8ac4fb166d2b58fd3f9bcae9b1be7c40f31a4))

- Correct return type annotation for create_or_update_workspace
  ([`0b8f06c`](https://github.com/johannehouweling/ToxTempAssistant/commit/0b8f06c5cea5b908459053c1fd7ae3c602606140))

- Logger __name__
  ([`33400c3`](https://github.com/johannehouweling/ToxTempAssistant/commit/33400c3a2a269289456502e1fd6ecd9092dcae36))

- Make tex export compile with pdflatex via iftex conditional
  ([`3fe868e`](https://github.com/johannehouweling/ToxTempAssistant/commit/3fe868eec9f8afa8c4480edb29ebca5aa5d8457e))

- Remove redundant inner try-except blocks in add_workspace_member*
  ([`bcc5c90`](https://github.com/johannehouweling/ToxTempAssistant/commit/bcc5c908b5470936268009cd8148db7c4461a6b3))

### Documentation

- Correct comment
  ([`3276495`](https://github.com/johannehouweling/ToxTempAssistant/commit/3276495cbe0ce9153693fd9c5b7b85f14c6b5a25))

### Features

- Add TEX export format for assay templates
  ([`8363187`](https://github.com/johannehouweling/ToxTempAssistant/commit/8363187fa91935a4abcb7be345e5b37b2e5a7c91))

### Refactoring

- Extract workspace views into toxtempass/workspace.py
  ([`d51d3e9`](https://github.com/johannehouweling/ToxTempAssistant/commit/d51d3e9c900cf601a1791045deaaea3636df7233))


## v3.8.1 (2026-04-30)

### Bug Fixes

- Enforce consistent investigation-owner guard across all workspace ops
  ([`4665211`](https://github.com/johannehouweling/ToxTempAssistant/commit/4665211826b1d7d5cb0b844946020fd03463ada8))

- Preserve investigation owner's perm on workspace deletion; add ownership docs
  ([`f12de39`](https://github.com/johannehouweling/ToxTempAssistant/commit/f12de39a2411eb928d601de2f517e2fbc19f8a40))

- Revoke guardian view_investigation perms on workspace deletion
  ([`23c994e`](https://github.com/johannehouweling/ToxTempAssistant/commit/23c994edd3ba04553c6391f72dc88081f4d98f8a))

- Row-lock during workspace deletion
  ([`03937a8`](https://github.com/johannehouweling/ToxTempAssistant/commit/03937a86c22be35d9f05bbc508d119c918512006))

- Use real add_workspace_assay view in test instead of direct assign_perm
  ([`7e7677e`](https://github.com/johannehouweling/ToxTempAssistant/commit/7e7677eb5d58cef666fe0f81631579a467d53c80))

- Use real views in _give_member_access; fix README ownership text and admonition style
  ([`d7a7265`](https://github.com/johannehouweling/ToxTempAssistant/commit/d7a7265d5b190915b9d4474d5efdbcd49ee011d2))

### Documentation

- Add workspace ownership model section to README
  ([`eb47e37`](https://github.com/johannehouweling/ToxTempAssistant/commit/eb47e37a0353845eced72737bb9b0c2009d8fc6a))

### Performance Improvements

- Further optimize delete_workspace to O(2) DB queries pre-loop
  ([`f82d3d1`](https://github.com/johannehouweling/ToxTempAssistant/commit/f82d3d1d598afd28bea5ac16e10e15f85f7cec40))

- Optimize delete_workspace perm revocation to O(members) queries
  ([`d63ae2d`](https://github.com/johannehouweling/ToxTempAssistant/commit/d63ae2dafc54fdd1eec0680db059db7554e9f62b))


## v3.8.0 (2026-04-29)

### Features

- Move description help text above textarea in create form (closes #issue)
  ([`e28f16f`](https://github.com/johannehouweling/ToxTempAssistant/commit/e28f16f78a6c19e6595cd1708f242789e11a1ee2))

### Refactoring

- Replace description help text with placeholder in AssayForm textarea
  ([`ec0f32e`](https://github.com/johannehouweling/ToxTempAssistant/commit/ec0f32ed952234915fa980f3388c71abc762ba5e))


## v3.7.0 (2026-04-29)

### Bug Fixes

- Address code review - remove redundant export_data check; clean up test syntax
  ([`8f80067`](https://github.com/johannehouweling/ToxTempAssistant/commit/8f800675f224cf066b9bb3f267827c379fcb2194))

- Address code review feedback on file parse error surfacing
  ([`68f57e4`](https://github.com/johannehouweling/ToxTempAssistant/commit/68f57e4a9b36d888401d4eb6f7c9f0a563b13a93))

### Documentation

- Document storage architecture (MinIO for persistent, tempfile for ephemeral exports)
  ([`81b43dd`](https://github.com/johannehouweling/ToxTempAssistant/commit/81b43dd6f59279080d50024b8406465314888026))

### Features

- Add error messages when TTA is unable to read uploaded files
  ([`972104e`](https://github.com/johannehouweling/ToxTempAssistant/commit/972104edbd57bd6580fba0a045cd8a0dc191e673))

- Remove media folder; use tempfile for exports, MinIO for all uploads
  ([`a527e3e`](https://github.com/johannehouweling/ToxTempAssistant/commit/a527e3ec59362eb12fb2c9da4a6859d7491a29d9))


## v3.6.3 (2026-04-29)

### Bug Fixes

- Reduce height of desciption boxes to 15vh
  ([`06dc4a8`](https://github.com/johannehouweling/ToxTempAssistant/commit/06dc4a8311d555c9f9d190c15fca78c235b0d26e))


## v3.6.2 (2026-04-29)

### Bug Fixes

- Collect files from all file inputs for upload progress bar
  ([`304c1a3`](https://github.com/johannehouweling/ToxTempAssistant/commit/304c1a32a8b79494eabe7c407e64294c0a060333))


## v3.6.1 (2026-04-29)

### Bug Fixes

- Increase description textarea min-height to 25vh for all three entity forms
  ([`b1d1418`](https://github.com/johannehouweling/ToxTempAssistant/commit/b1d1418e87937441be453e0eefd5d201d0c208c3))


## v3.6.0 (2026-04-29)

### Bug Fixes

- Add file accumulator to update modal to preserve selected files across picker opens
  ([`21927f1`](https://github.com/johannehouweling/ToxTempAssistant/commit/21927f18f9a0399a4fda324497095fdae7e8c64f))

- Imporve file indentivfaction when removeing files from file list
  ([`35eeab6`](https://github.com/johannehouweling/ToxTempAssistant/commit/35eeab6d72eff7a9d4c9c0c5aa7c6d2455e24bfe))

- Small tweaks for doc imports loading
  ([`afd3404`](https://github.com/johannehouweling/ToxTempAssistant/commit/afd34046838321e75cd51454d261e484552297ae))

- **file-upload**: Multi-file select also for modal per-question upload
  ([`c9f90b0`](https://github.com/johannehouweling/ToxTempAssistant/commit/c9f90b04e9626d95ceda5338290ceb0810616c36))

### Documentation

- Remove OpenAI API references from documentation
  ([`c44900b`](https://github.com/johannehouweling/ToxTempAssistant/commit/c44900b3787b2afc51d3825fbc3295be9c8ec2f1))

### Features

- **file-upload**: Multi-file select also for modal per-question upload
  ([`54e906e`](https://github.com/johannehouweling/ToxTempAssistant/commit/54e906e26b1d8bd7d16c76420f5f84f876339c07))

### Refactoring

- Purge OpenRouter from codebase, keep OPENAI_API_KEY functional
  ([`9992dec`](https://github.com/johannehouweling/ToxTempAssistant/commit/9992dec7edca0ee5c8d6438dbb17289586487ebb))

- Use arrow function instead of IIFE in renderAccumulatedFileList
  ([`0034ccd`](https://github.com/johannehouweling/ToxTempAssistant/commit/0034ccdc8d87e5162ae87c61be7fdff7264a9a8c))


## v3.5.0 (2026-04-29)

### Documentation

- Add citation instructions to README
  ([`d58423f`](https://github.com/johannehouweling/ToxTempAssistant/commit/d58423fb4e8cded1abb4cb621861cb27820badf7))

### Features

- Add how to cite sentence and fix swapped footnote links on landing page
  ([`1acc0bb`](https://github.com/johannehouweling/ToxTempAssistant/commit/1acc0bbe98ea90adac2e85d643c0e091dfabe849))


## v3.4.1 (2026-04-27)

### Bug Fixes

- Redirect admitted users away from beta wait page to overview
  ([`b251ddb`](https://github.com/johannehouweling/ToxTempAssistant/commit/b251ddbc0cff8704ad86b3a3bee452a4dc62fb07))


## v3.4.0 (2026-04-27)

### Documentation

- Fix inline docs
  ([`4b4c169`](https://github.com/johannehouweling/ToxTempAssistant/commit/4b4c169c05c9c7b12a97a13847f553ae1984114f))

- Fix typo
  ([`6137e32`](https://github.com/johannehouweling/ToxTempAssistant/commit/6137e325374c9e742cc060b11690a6af03175736))

- Fix typo
  ([`0eea1f8`](https://github.com/johannehouweling/ToxTempAssistant/commit/0eea1f8a0a5ce22904f51557214aeb837e7272ae))

### Features

- Enhance context truncation logic and clean up user alerts handling
  ([`d3662b5`](https://github.com/johannehouweling/ToxTempAssistant/commit/d3662b536112ca091cebad3800e71b9932c78b7c))

- Use per-model context-window tag for PDF context budget
  ([`b9d066f`](https://github.com/johannehouweling/ToxTempAssistant/commit/b9d066f2750d0ba302d49c0637c33d58f713f2a0))

- **alert**: Refactor status_context to processing_log and add user_alerts for user notifications
  ([`8b175c9`](https://github.com/johannehouweling/ToxTempAssistant/commit/8b175c989ba691cf4176c5debf49f05d93a92e70))

### Refactoring

- Move get_azure_model import to module level in views.py
  ([`8e9d022`](https://github.com/johannehouweling/ToxTempAssistant/commit/8e9d022d76f001c4380ba19c4dec84d62b152b4c))


## v3.3.0 (2026-04-25)

### Bug Fixes

- Noreferrer
  ([`6c0b618`](https://github.com/johannehouweling/ToxTempAssistant/commit/6c0b618e566a49ed85d016580e6a49c40abdbe65))

### Features

- **llm**: Add context-window tag support in model entry and update .env.dummy
  ([`6f0a310`](https://github.com/johannehouweling/ToxTempAssistant/commit/6f0a3109749bd78b861c21f97c8a167047005c59))

- **llm**: Add context_window to LLM info and user offcanvas display
  ([`bc5c331`](https://github.com/johannehouweling/ToxTempAssistant/commit/bc5c331e5e97f89f3389a569bfca9c6694b4f35d))


## v3.2.0 (2026-04-25)

### Features

- Add pytest-cov coverage reporting to PRs via GitHub Actions
  ([`ebabb44`](https://github.com/johannehouweling/ToxTempAssistant/commit/ebabb444f599b1377e4d9a583a50becc310f7e58))


## v3.1.4 (2026-04-25)

### Bug Fixes

- Edge case where llm_model is None
  ([`07ebac4`](https://github.com/johannehouweling/ToxTempAssistant/commit/07ebac4360878914789a3e4e024200047d6c0833))

- Edge case where llm_model is None
  ([`a856c3a`](https://github.com/johannehouweling/ToxTempAssistant/commit/a856c3af735b5195b141b6b15c661a76349d48ef))

- **ci**: Potential fix for code scanning alert no. 29: Workflow does not contain permissions
  ([`68c4d92`](https://github.com/johannehouweling/ToxTempAssistant/commit/68c4d92a4f07a1a23aeab3af0eec74c1dac1d34e))

### Documentation

- Add CLAUDE.md for project guidance and common commands
  ([`c525373`](https://github.com/johannehouweling/ToxTempAssistant/commit/c5253739a4cee8506e82df194866e82976034286))

### Refactoring

- Update type hints for beta utilities and remove unused import
  ([`e9ec7c9`](https://github.com/johannehouweling/ToxTempAssistant/commit/e9ec7c967cc2a40948684c6fcb8227acf5857150))

- **beta**: Implement atomic preference updates for user settings
  ([`2e94e24`](https://github.com/johannehouweling/ToxTempAssistant/commit/2e94e24a056a7e5d189112be73c6249e9287ded8))


## v3.1.3 (2026-04-25)

### Bug Fixes

- Update dependencies for Faker, pytest, and pytest-cov to latest versions
  ([`f65b40e`](https://github.com/johannehouweling/ToxTempAssistant/commit/f65b40eb0327a9747d2ade3c93287f0eedcbc5bd))


## v3.1.2 (2026-04-25)

### Bug Fixes

- Address review feedback - spelling, trimming logic, HTTP status codes, settings mkdir, and add
  tests
  ([`0fd5705`](https://github.com/johannehouweling/ToxTempAssistant/commit/0fd5705f309880b7f3d9cc2145b7255f757927b2))

- Centralize CSRF token definition in base.html and remove redundant code in workspace_js.html and
  new.html
  ([`bf1a0e9`](https://github.com/johannehouweling/ToxTempAssistant/commit/bf1a0e9d1fb3a38cb4102e7a0955f77fa73730f5))

- Correct error message formatting in add_status_context function
  ([`a0e4b2f`](https://github.com/johannehouweling/ToxTempAssistant/commit/a0e4b2f623af68150c2eb758c71375a17d62487a))

- Enforce POST method for delete endpoints and update related templates and tests also in tables
  ([`837d9c6`](https://github.com/johannehouweling/ToxTempAssistant/commit/837d9c60b371c472da6f494a1944a92c002bf15d))

- Enforce POST method for workspace deletion and update tests accordingly
  ([`8a41078`](https://github.com/johannehouweling/ToxTempAssistant/commit/8a4107808d52f1f8c3a9c0eed9daab2a8892be06))

- Enhance error handling and logging with correlation IDs for better traceability
  ([`18e9276`](https://github.com/johannehouweling/ToxTempAssistant/commit/18e92761a1b88af19f3b788ae1d67d3a17f6881e))

- Improve error message formatting and enhance readability in views
  ([`8f1c899`](https://github.com/johannehouweling/ToxTempAssistant/commit/8f1c899b2f64f764768ed88d201ed51d454b738f))

- Standardize error response messages and improve HTTP status codes in views
  ([`fa6ee60`](https://github.com/johannehouweling/ToxTempAssistant/commit/fa6ee60f915542297af52e49d90fc0e0e58db2db))

- Update email generation in PersonFactory for unique test emails
  ([`91d32ab`](https://github.com/johannehouweling/ToxTempAssistant/commit/91d32abd74ae7b37b73a06e471636639aa28a385))

### Chores

- Update poetry lock
  ([`4d504ce`](https://github.com/johannehouweling/ToxTempAssistant/commit/4d504ce8a45a71490742c9747c1991dd0d2d7cb8))

### Refactoring

- Enhance error handling and logging in export and view functions
  ([`98ccd9a`](https://github.com/johannehouweling/ToxTempAssistant/commit/98ccd9a5fe8f238836f9351269c106b7b871f104))


## v3.1.1 (2026-04-25)

### Bug Fixes

- **ci**: Enable BuildKit caching for Docker builds to improve CI performance
  ([`f111714`](https://github.com/johannehouweling/ToxTempAssistant/commit/f11171411ac5e6cdb9d17bc8bcc4140553c46c37))


## v3.1.0 (2026-04-25)

### Documentation

- Update README and Dockerfile to include BuildKit requirements for improved build performance
  ([`4036c54`](https://github.com/johannehouweling/ToxTempAssistant/commit/4036c540a03437c3ee4d5a0e583c35a11e1e95bf))

### Features

- **ci**: Enhance caching in CI workflow and Docker Compose for improved build performance
  ([`c9a8b13`](https://github.com/johannehouweling/ToxTempAssistant/commit/c9a8b13a14278fa166e22c54c0ec79a8351e8619))

- **docker**: Add .dockerignore and improve Dockerfile for caching and installation efficiency
  ([`06fbb9b`](https://github.com/johannehouweling/ToxTempAssistant/commit/06fbb9b19fafbe5481b5ebff308f9445f4e19550))


## v3.0.2 (2026-04-25)

### Bug Fixes

- Close FileResponse handles in test_export_security to prevent temp-dir leaks
  ([`184598b`](https://github.com/johannehouweling/ToxTempAssistant/commit/184598bb40df3725a5c246aafafde0ef40b2d1ec))

- Implement _release_file_response to properly close FileResponse handles in tests
  ([`4abf5cb`](https://github.com/johannehouweling/ToxTempAssistant/commit/4abf5cb687436436edb954276a0ae6225c3ed64b))

- Update myocyte/toxtempass/export.py
  ([`c8a204e`](https://github.com/johannehouweling/ToxTempAssistant/commit/c8a204ee43e41431aee996b767afee7999098212))

- **security**: Potential fix for code scanning alert no. 7
  ([`ffce48b`](https://github.com/johannehouweling/ToxTempAssistant/commit/ffce48b970484172ed844ebd97080827a3ed7931))

### Chores

- **dependencies**: Update package versions for compatibility
  ([`dbf6adc`](https://github.com/johannehouweling/ToxTempAssistant/commit/dbf6adca9bf84c84278b9c3ee4f5f07db31091a8))

- **poetry**: Bump versions
  ([`d2ba824`](https://github.com/johannehouweling/ToxTempAssistant/commit/d2ba82422f62482252a7ca392b4361f4dea5eaca))

### Refactoring

- Immutable EXPORT_MAPPING, PANDOC_EXPORT_TYPES constant, and security regression tests
  ([`8b3ec70`](https://github.com/johannehouweling/ToxTempAssistant/commit/8b3ec704805fbd35c1ad9d840578daf5ea3a9a5c))

- Move export_mapping to module-level constant and fix filename double-dot
  ([`2761e58`](https://github.com/johannehouweling/ToxTempAssistant/commit/2761e5834b0994b2f3c792ca71264efb4f7f2d0d))

- Update export and file handling to use immutable constants for file types and MIME mappings
  ([`4024101`](https://github.com/johannehouweling/ToxTempAssistant/commit/40241014613746fa3ac77688e041e571a27836b5))


## v3.0.1 (2026-04-13)

### Bug Fixes

- **admin**: Use mark_safe for static env-default badge
  ([`9dbe5ff`](https://github.com/johannehouweling/ToxTempAssistant/commit/9dbe5ffc58b7b8d45ca8260d7f9fed52bcb4b009))


## v3.0.0 (2026-04-12)

### Chores

- **deps**: Bump langchain-core from 1.2.15 to 1.2.22
  ([`1b439eb`](https://github.com/johannehouweling/ToxTempAssistant/commit/1b439ebedb1cc677ac7dc8ceb50d2541fd12c628))

- **deps**: Bump pypdf from 6.7.3 to 6.9.2
  ([`e6fb716`](https://github.com/johannehouweling/ToxTempAssistant/commit/e6fb716c90917785e42e164ca3ce6da1e7373f58))

### Features

- **azure**: Add Azure AI Foundry integration and health check command
  ([`7178047`](https://github.com/johannehouweling/ToxTempAssistant/commit/71780472b2c25bcbeac6f336e0289b11793d34d6))


## v2.16.2 (2026-03-27)

### Bug Fixes

- **.gitignore**: Add .claude to backups exclusion
  ([`6e4fb89`](https://github.com/johannehouweling/ToxTempAssistant/commit/6e4fb898b711133b1ee50137ba665b2dd4fe2a60))

- **deploy**: Suppress error when connecting to docker network
  ([`58f6651`](https://github.com/johannehouweling/ToxTempAssistant/commit/58f6651434f96ed3151e8fd4145134b9ea5ba3e8))


## v2.16.1 (2026-03-27)

### Bug Fixes

- **migrations**: Remove stale django-q schedule for cleanup_orphaned_files
  ([`eabfc45`](https://github.com/johannehouweling/ToxTempAssistant/commit/eabfc4538b39bc79e2047ad8e615ccb91f79d937))

### Chores

- **deps**: Bump django from 6.0.2 to 6.0.3
  ([`0244168`](https://github.com/johannehouweling/ToxTempAssistant/commit/024416820e27fb29db7bfba1351ec02ee2983588))

- **deps-dev**: Bump tornado from 6.5.4 to 6.5.5
  ([`ca518eb`](https://github.com/johannehouweling/ToxTempAssistant/commit/ca518eb9777dbabd1cd354294b785d568558e202))


## v2.16.0 (2026-03-06)

### Bug Fixes

- **groups**: Fix permission revocation logic for workspace members
  ([`0260dad`](https://github.com/johannehouweling/ToxTempAssistant/commit/0260dad18d043dcf52d106dbed43af535d892840))

- **workspaces**: Implement delete permissions for assays based on ownership
  ([`6979f9f`](https://github.com/johannehouweling/ToxTempAssistant/commit/6979f9f5a27ead21c2e912aef846a4ee8f9fa201))

### Features

- **groups**: Add functionality to manage group members by email and enhance group list UI
  ([`f5ce910`](https://github.com/johannehouweling/ToxTempAssistant/commit/f5ce9109a8ffcf0059e2f3ecdc5cd1e38ce18afd))

- **groups**: Enhance group UI and add group assay response
  ([`7ac9457`](https://github.com/johannehouweling/ToxTempAssistant/commit/7ac94577d1aef78e2ae5600fa8dca4be71ae9582))

- **groups**: Switched to "workspace" instead of "group" - moved to offcanvas.
  ([`d6e6f03`](https://github.com/johannehouweling/ToxTempAssistant/commit/d6e6f0344b320d1398354a613edcc74a9ba41f87))

- **groups**: Update essay permission
  ([`8928377`](https://github.com/johannehouweling/ToxTempAssistant/commit/892837753fa6a8e16340c6cdc3522ccaa98a1349))

- **migrations**: Fix migration (actually compiled)
  ([`2411d13`](https://github.com/johannehouweling/ToxTempAssistant/commit/2411d133957c5599526bfaf45eff44c4ad8547ab))

- **upload**: Enhance file upload progress display and implement file accumulation
  ([`c8eb1fc`](https://github.com/johannehouweling/ToxTempAssistant/commit/c8eb1fcf612a9f082f381d61c86b44091acbe14d))

- **workspaces**: Workspace fine-tuning
  ([`b85c8fe`](https://github.com/johannehouweling/ToxTempAssistant/commit/b85c8fe78c46af50ab05f545bd47d3a534aeff7d))


## v2.15.0 (2026-02-27)

### Features

- **nginx**: 403 forbidden resource page
  ([`685502c`](https://github.com/johannehouweling/ToxTempAssistant/commit/685502ccb2f906f5eb0071086585af314a60a1df))

- **nginx**: Add custom HTML error pages for 400, 401, 404, and 500 responses
  ([`83ce52e`](https://github.com/johannehouweling/ToxTempAssistant/commit/83ce52e6c3cbe8b378cb8bfb6b3672b7198edc83))


## v2.14.5 (2026-02-26)

### Bug Fixes

- **forms**: Correct spelling of "Modify" in button labels for StartingForm
  ([`571da13`](https://github.com/johannehouweling/ToxTempAssistant/commit/571da13081b16d0732e3bf244e83b61575ed27bc))


## v2.14.4 (2026-02-25)

### Bug Fixes

- **backup**: Enhance backup script documentation and clarify media directory handling
  ([`cd10325`](https://github.com/johannehouweling/ToxTempAssistant/commit/cd10325840b8e98a68bdbec8f776cafede08cec5))


## v2.14.3 (2026-02-25)

### Bug Fixes

- **backup**: Improve MinIO backup path handling for container and host execution
  ([`ac50db7`](https://github.com/johannehouweling/ToxTempAssistant/commit/ac50db75a462bf73cff70d596a385db650d006e9))


## v2.14.2 (2026-02-25)

### Bug Fixes

- **backup**: Add container detection for MinIO backup execution to export at backup location not
  absolute
  ([`391f793`](https://github.com/johannehouweling/ToxTempAssistant/commit/391f7939de6469a7f13614521a5661e421005b45))


## v2.14.1 (2026-02-24)

### Bug Fixes

- **tables**: Replace format_html with mark_safe for safe HTML rendering
  ([`9b31bb0`](https://github.com/johannehouweling/ToxTempAssistant/commit/9b31bb060ef17b041892682bc4ea6d4c2628e2a3))


## v2.14.0 (2026-02-24)

### Features

- **maintenance**: Update dependencies and Python version in pyproject.toml
  ([`d15bfec`](https://github.com/johannehouweling/ToxTempAssistant/commit/d15bfec43cae47ea7a470b7eb93c02ac0ba80573))


## v2.13.3 (2026-02-24)

### Bug Fixes

- **docker**: Add missing ca-certificates and curl packages to Dockerfile
  ([`d1215fe`](https://github.com/johannehouweling/ToxTempAssistant/commit/d1215fe2aea946eea1784e8c5e6cbfacd38a7e4e))


## v2.13.2 (2026-02-24)

### Bug Fixes

- **tables**: Depreciation of accessor path traversal via dot instead of __
  ([`9273b94`](https://github.com/johannehouweling/ToxTempAssistant/commit/9273b94c6921ac24c942d08543145ee1b5919989))

### Refactoring

- **backup**: Rename backup_scheduler service to backup and update entrypoint script for supercronic
  ([`472468c`](https://github.com/johannehouweling/ToxTempAssistant/commit/472468c566503bacc7dc66c8dea5519d9fdd300b))


## v2.13.1 (2026-02-24)

### Bug Fixes

- **docker**: Correct entrypoint.sh path in Dockerfile
  ([`819cc53`](https://github.com/johannehouweling/ToxTempAssistant/commit/819cc53c0ddc85f02e2a9151e453e8f5f7353a50))


## v2.13.0 (2026-02-24)

### Bug Fixes

- **.gitignore**: Add backups section
  ([`a0f52ef`](https://github.com/johannehouweling/ToxTempAssistant/commit/a0f52ef17a74a8e7745afb724fe13715ff1e1797))

### Features

- **backup**: Implement backup scheduler with cron job and environment configuration
  ([`dc46193`](https://github.com/johannehouweling/ToxTempAssistant/commit/dc46193016317318f98081ff3be008ff72ad218d))


## v2.12.4 (2026-02-23)

### Bug Fixes

- **backup**: Enhance retention cleanup with safety checks for BACKUP_ROOT and timestamp validation
  ([`39db889`](https://github.com/johannehouweling/ToxTempAssistant/commit/39db889d3f214f6470741f741ef87d125ed357e2))


## v2.12.3 (2026-02-23)

### Bug Fixes

- **backup**: Enhance MinIO mirror command with environment variables for endpoint and credentials
  ([`995c04c`](https://github.com/johannehouweling/ToxTempAssistant/commit/995c04c644b6db14c0712acdefd4f03ad8e7ee28))


## v2.12.2 (2026-02-23)

### Bug Fixes

- **env**: Correct BACKUP_ROOT path in .env.dummy [no-ci]
  ([`dbdc8ef`](https://github.com/johannehouweling/ToxTempAssistant/commit/dbdc8efb0f0857353eea2bfb013716fa71e486de))


## v2.12.1 (2026-02-23)

### Bug Fixes

- **backup**: Update MinIO mirror command to use local alias and correct destination path [no-ci]
  ([`813b14d`](https://github.com/johannehouweling/ToxTempAssistant/commit/813b14dbb6103ce089facf10874764c08172ee3a))


## v2.12.0 (2026-02-23)

### Chores

- **deps**: Bump aiohttp from 3.13.2 to 3.13.3
  ([`577f20c`](https://github.com/johannehouweling/ToxTempAssistant/commit/577f20c7fe3e4b5b67fd099bb13e690e333a41ea))

- **deps**: Bump cryptography from 46.0.3 to 46.0.5
  ([`e89f888`](https://github.com/johannehouweling/ToxTempAssistant/commit/e89f8880f34fb6f923d9b7e05372faa0c23a1439))

- **deps**: Bump django from 5.2.9 to 5.2.11
  ([`088404d`](https://github.com/johannehouweling/ToxTempAssistant/commit/088404da1f6277fd2168173a25b8a449dfedfa04))

- **deps**: Bump langchain-core from 0.3.80 to 0.3.81
  ([`656471a`](https://github.com/johannehouweling/ToxTempAssistant/commit/656471ad5f78266fd847efe8ca3f07c8f0f82305))

- **deps**: Bump urllib3 from 2.6.1 to 2.6.3
  ([`6cb327d`](https://github.com/johannehouweling/ToxTempAssistant/commit/6cb327d1f80f3b354accb6e9f16e859266631f95))

### Features

- **backup**: Add backup script and update .env for backup configuration [no-ci]
  ([`86b259e`](https://github.com/johannehouweling/ToxTempAssistant/commit/86b259e75357402889f2e8a89c734f4c22a6bde4))


## v2.11.1 (2026-01-20)

### Bug Fixes

- **forms**: Consent to upload files is no longer mandatory but actually optional with GDPR
  compliant details page.
  ([`036c613`](https://github.com/johannehouweling/ToxTempAssistant/commit/036c61336f44af82dbefda3083ac20dd1afc0eae))


## v2.11.0 (2026-01-19)

### Bug Fixes

- **ui**: Update busy status button to show loading spinner
  ([`f035981`](https://github.com/johannehouweling/ToxTempAssistant/commit/f0359816ef3f2c51c48f9ff4a15963310a8f4fe2))

### Features

- **ui**: Implement polling for assay status updates and add configuration for reload intervals
  ([`2b8248d`](https://github.com/johannehouweling/ToxTempAssistant/commit/2b8248d18bf590de35f1d0b7cf5cfdd250c8a517))


## v2.10.1 (2026-01-19)

### Bug Fixes

- **ui**: Enhance button tooltip messages for scheduled and busy statuses
  ([`e75830a`](https://github.com/johannehouweling/ToxTempAssistant/commit/e75830a39434dcb2882e17ff24b925de1af1db73))


## v2.10.0 (2026-01-19)

### Features

- **ui**: Add property to count answers found but not accepted and update progress bar rendering
  ([`2a0c5a7`](https://github.com/johannehouweling/ToxTempAssistant/commit/2a0c5a7e2825d99c3268bdbe163aaefc474b11c7))


## v2.9.2 (2026-01-13)

### Bug Fixes

- **demo**: Change filter to get for demo_assay retrieval
  ([`31cfe56`](https://github.com/johannehouweling/ToxTempAssistant/commit/31cfe56ccee8c9a9fc5088a8927a5b68fc4e62ef))

- **demo**: Fix the test to work with automatic signals based demo assay creation
  ([`42bc81b`](https://github.com/johannehouweling/ToxTempAssistant/commit/42bc81b9c611323cf7244edef33f592ad0a3e15d))

- **demo**: Test
  ([`ca5d186`](https://github.com/johannehouweling/ToxTempAssistant/commit/ca5d186bed46206d86cc7aae0b7342e634d756a5))

- **demo**: Wrong import path for seed_demo_assay_for_user func
  ([`a465848`](https://github.com/johannehouweling/ToxTempAssistant/commit/a465848812f650b872e1056bb5a92f1a5573874d))

- **signals**: Import signals module in app configuration to ensure signal handling
  ([`57671fe`](https://github.com/johannehouweling/ToxTempAssistant/commit/57671fefa3db7416437c5d37a023167b074820c2))

- **storages**: Remove broken orphaned file handling logic
  ([`f90745e`](https://github.com/johannehouweling/ToxTempAssistant/commit/f90745e433cee2b9a93c655379182f4c67c7733a))

- **storages**: Still remove dangling orphan function registration
  ([`42d1c18`](https://github.com/johannehouweling/ToxTempAssistant/commit/42d1c18d2e518ba53046ff8202f80b45bc86ea0a))


## v2.9.1 (2026-01-12)

### Bug Fixes

- <trigger release only>
  ([`961dc3c`](https://github.com/johannehouweling/ToxTempAssistant/commit/961dc3c6155f318be935df90e579154130cebeaa))

### Refactoring

- **demo**: Demo assays are now created when a new user is added to db from one central location
  (signals.py)
  ([`4434cd2`](https://github.com/johannehouweling/ToxTempAssistant/commit/4434cd2ae2fa76ed61350859d5e61a7921132825))


## v2.9.0 (2026-01-12)

### Bug Fixes

- **file storage**: Add logging for file downloads and implement download action for assays
  ([`80266b7`](https://github.com/johannehouweling/ToxTempAssistant/commit/80266b7142e9d7c0b6e6667322cb4088337d5368))

- **file storage**: Correct S3 object key format for user documents
  ([`1c93f42`](https://github.com/johannehouweling/ToxTempAssistant/commit/1c93f42d410d33f3188a5aa07fee3baf3eb1d34c))

- **file storage**: Fix how files are linked to assays
  ([`1d226cb`](https://github.com/johannehouweling/ToxTempAssistant/commit/1d226cb09ccd62da4adcdc6db3c085bc8488321a))

- **file storage**: Wrap zip_bytes in BytesIO for FileResponse in AssayAdmin
  ([`51589ca`](https://github.com/johannehouweling/ToxTempAssistant/commit/51589ca431d9b953d5274e87a3c5396704b67e5f))

- **image processing**: Increase minimum image dimensions and filter out small images in conversion
  ([`f68e9e3`](https://github.com/johannehouweling/ToxTempAssistant/commit/f68e9e3e5e65fba98b005a4c630224c6307b8d28))

- **storages**: Actually make AWS env vars availble to settings.py
  ([`cd33c48`](https://github.com/johannehouweling/ToxTempAssistant/commit/cd33c489ed14a06b53297507c5a6afd1ae32aed5))

- **storages**: Make sure that djangoapp has minio available
  ([`524f9e2`](https://github.com/johannehouweling/ToxTempAssistant/commit/524f9e201a1cf968458c0190eb456d015fbd13b3))

- **tests**: Update image sizes in PDF and DOCX extraction tests for consistency
  ([`28acf61`](https://github.com/johannehouweling/ToxTempAssistant/commit/28acf616d73ece5cbea1459e5d85f2a2ac74854c))

- **tests**: Update object key path in file storage consent test for accuracy
  ([`75c0cbf`](https://github.com/johannehouweling/ToxTempAssistant/commit/75c0cbf9329c15f0ffb77353ee99fc0dbd02e1cf))

### Chores

- **ci**: Update poetry lock
  ([`444dde5`](https://github.com/johannehouweling/ToxTempAssistant/commit/444dde54ed55dfa80397902fa3d6f465dfd63aa0))

### Features

- **factory**: Add factories for QuestionSet, Section, Subsection, Question, Answer, and FileAsset
  models
  ([`c89aae0`](https://github.com/johannehouweling/ToxTempAssistant/commit/c89aae009fcfc9b71227148920e0754e07e316c5))

- **file storage**: Implement user consent for file storage and add cleanup commands
  ([`9b5c43c`](https://github.com/johannehouweling/ToxTempAssistant/commit/9b5c43ce2e25f0f2131731d0121b7fd471fbfc6e))

- **uploadfiles**: Consent button on upload to store files for improvements.
  ([`6f5241c`](https://github.com/johannehouweling/ToxTempAssistant/commit/6f5241c2cfee8f343b4f488826c2933d33ec3a7f))

### Refactoring

- **tests**: Remove orphaned file cleanup tests
  ([`ee61d51`](https://github.com/johannehouweling/ToxTempAssistant/commit/ee61d51da010b0c4687b9d4bf54f7e8ca6e0426b))


## v2.8.0 (2026-01-11)

### Bug Fixes

- **storages**: Staticfiles on server as per usual
  ([`daba64b`](https://github.com/johannehouweling/ToxTempAssistant/commit/daba64b96f4fcdc75a8ee1c7275973f0951a4186))

- **ui**: Tighten helper text on StartingForm's FileField
  ([`6097abc`](https://github.com/johannehouweling/ToxTempAssistant/commit/6097abca35184157e5e482f6fc84f30890793fee))

### Features

- **new.html**: Add popover for file upload field help text with supported formats
  ([`9f1037d`](https://github.com/johannehouweling/ToxTempAssistant/commit/9f1037d4558fe1509ed160fc797ee6a4ad77c6a6))

- **storage**: Integrate django-storages for S3 support and update dependencies
  ([`9afbd34`](https://github.com/johannehouweling/ToxTempAssistant/commit/9afbd343ea387d1daafa322d08d3bbab435ebc54))

- **storages**: Add FileAsset and AnswerFile models with storage deletion logic
  ([`92d171b`](https://github.com/johannehouweling/ToxTempAssistant/commit/92d171b3072fb00f0a3a3e358008fd78b2c32df5))


## v2.4.1 (2026-01-10)

### Bug Fixes

- **minio**: Use latest version fo minio client
  ([`95a7274`](https://github.com/johannehouweling/ToxTempAssistant/commit/95a72740ece21488ae882af250dc19408e081d29))

### Chores

- **ci**: Bump version of actions
  ([`4446c57`](https://github.com/johannehouweling/ToxTempAssistant/commit/4446c57041205ace65642b1d3b2ca30cf184408a))


## v2.4.0 (2026-01-10)

### Bug Fixes

- **minio**: Add profiles
  ([`8572ba9`](https://github.com/johannehouweling/ToxTempAssistant/commit/8572ba9130514a97bf3bec4953332f0748bea7a0))

- **minio**: Attach minio to network
  ([`81a335b`](https://github.com/johannehouweling/ToxTempAssistant/commit/81a335b3afe69d2155052ed9b3e113eda801c061))

### Features

- **minio**: Add MinIO initialization script and update docker-compose
  ([`c29f9eb`](https://github.com/johannehouweling/ToxTempAssistant/commit/c29f9eb6d3a4a43840882716804f693e87de4840))


## v2.3.1 (2026-01-10)

### Bug Fixes

- **minio**: Correct Release
  ([`e387837`](https://github.com/johannehouweling/ToxTempAssistant/commit/e38783782975a1f69dc0db833b982479e798ed3d))


## v2.3.0 (2026-01-10)

### Bug Fixes

- **evaluation**: Update input directory path for quality scoring to use relative path
  ([`9f98a8e`](https://github.com/johannehouweling/ToxTempAssistant/commit/9f98a8e9f9d4844498648cf2cea7eb1df83af602))

- **poetry**: Update lockfile
  ([`2eb8988`](https://github.com/johannehouweling/ToxTempAssistant/commit/2eb898840d3b4deafb59fea9bf066913c9356a25))

- **poetry**: Update poetry lock file
  ([`2375150`](https://github.com/johannehouweling/ToxTempAssistant/commit/2375150c9c5f51558de295b9926dd0b64f4efb59))

### Chores

- **ci**: Deleted cleanup scripts
  ([`080070f`](https://github.com/johannehouweling/ToxTempAssistant/commit/080070fca32a8c35064703e0413b3748d6516219))

- **pyproject**: Update version to 2.2.0 and modify dependencies for compatibility
  ([`de8c089`](https://github.com/johannehouweling/ToxTempAssistant/commit/de8c08943687c462779e1ba49f4d93ae9ba627bb))

### Documentation

- **config**: Update notes on evaluation metrics and BERT usage in EvaluationConfig
  ([`4780266`](https://github.com/johannehouweling/ToxTempAssistant/commit/478026655ece897e4e1849f534d53c81f05f456c))

- **config**: Update notes on evaluation metrics and BERT usage in EvaluationConfig
  ([`d9eb306`](https://github.com/johannehouweling/ToxTempAssistant/commit/d9eb306b1bd750a04231dbd76c00c2554df3ff64))

- **urls**: Add example usage comment for init_db path
  ([`8ceea9f`](https://github.com/johannehouweling/ToxTempAssistant/commit/8ceea9fbfd0b11d92e8679b52e30dd72cbbf7267))

- **urls**: Add example usage comment for init_db path
  ([`0dae720`](https://github.com/johannehouweling/ToxTempAssistant/commit/0dae7205bef82a1cc15ad647a045225b7456392f))

### Features

- **data_analysis**: Update model handling and enhance scatter plot category orders for consistency
  ([`a401a23`](https://github.com/johannehouweling/ToxTempAssistant/commit/a401a2307051bcfc6db7d8782cf760112dc4e105))

- **evaluation**: Add validation metrics and BERT score options to ExperimentConfig and related
  functions
  ([`1bc2553`](https://github.com/johannehouweling/ToxTempAssistant/commit/1bc2553b496aa6a05790f87169129931c5994f3e))

- **evaluation**: Add validation metrics and BERT score options to ExperimentConfig and related
  functions
  ([`8b5f322`](https://github.com/johannehouweling/ToxTempAssistant/commit/8b5f322f45edb082d89b98030d2875f23a7c242f))

- **init_db**: Implement command to create QuestionSet from JSON file
  ([`b1438a2`](https://github.com/johannehouweling/ToxTempAssistant/commit/b1438a2000ca704b8f5c22ab67ad3a44995cf474))

- **init_db**: Implement command to create QuestionSet from JSON file
  ([`1cbc8fe`](https://github.com/johannehouweling/ToxTempAssistant/commit/1cbc8fee5d26fb5949b2d6fdc0bda110284892f6))

- **licenses**: Add third-party licenses documentation for OECD and EFSA materials
  ([`89d0cce`](https://github.com/johannehouweling/ToxTempAssistant/commit/89d0ccef98276f94ffb4ce964f504f10f6ade306))

- **minio**: Added minio for blob storage option
  ([`789ebb6`](https://github.com/johannehouweling/ToxTempAssistant/commit/789ebb6321755f2fc390602b55f2a599998396a4))

- **neg_summary, pooled_summary**: Refactor model file handling and enhance accuracy plotting with
  error bars
  ([`28d73e3`](https://github.com/johannehouweling/ToxTempAssistant/commit/28d73e3c3a22ba48a5146ecaa5e0b0025825b5c5))

- **pooled_summary**: Enhance accuracy scatter plot with improved legend and error bar labels
  ([`85e4b8f`](https://github.com/johannehouweling/ToxTempAssistant/commit/85e4b8f4cd68d6467be81fcfaf2c27f52919d4c1))

- **pooled_summary**: Enhance scatter plot styling with updated color and error bar settings
  ([`cfe6c9a`](https://github.com/johannehouweling/ToxTempAssistant/commit/cfe6c9aa681852b96a184e1a9533a6091051b98e))

- **questionset**: Enhance create_questionset_from_json to reuse existing QuestionSet if empty
  ([`d5e6470`](https://github.com/johannehouweling/ToxTempAssistant/commit/d5e6470c701ca1b29a86cf0d7bacd9ba37c06704))

- **questionset**: Enhance create_questionset_from_json to reuse existing QuestionSet if empty
  ([`cb2a708`](https://github.com/johannehouweling/ToxTempAssistant/commit/cb2a708145ea88342d721ca3dbdc53d0679851aa))

### Refactoring

- **data_analysis**: Add ncontrol_summary and pcontrol_summary scripts
  ([`d057ec7`](https://github.com/johannehouweling/ToxTempAssistant/commit/d057ec78b573ef2ce11deb67c671d893f934c86f))

- **data_analysis**: Remove unused neg_summary and pos_summary scripts; update pcontrol_summary for
  improved directory handling and output paths
  ([`f3780c7`](https://github.com/johannehouweling/ToxTempAssistant/commit/f3780c7d4bef6542466d4e7083d076f71ba9b874))

- **evaluation**: Remove BERT score option from ExperimentConfig
  ([`74f3e6e`](https://github.com/johannehouweling/ToxTempAssistant/commit/74f3e6e34d766800e1c8560e0a237ad1064658b4))

- **evaluation**: Remove BERT score option from ExperimentConfig
  ([`b64bb1c`](https://github.com/johannehouweling/ToxTempAssistant/commit/b64bb1cc68835bb1ad8d4dfbd8adcc442ab64345))


## v2.2.0 (2025-12-01)

### Bug Fixes

- **ci**: Prevent the lfs and throw away artifacts after a while
  ([`e717cbe`](https://github.com/johannehouweling/ToxTempAssistant/commit/e717cbe383fcec6baeb7d4a5618334e35ffe4b9f))

- **image**: Only extract image if extrac_images is True
  ([`c969183`](https://github.com/johannehouweling/ToxTempAssistant/commit/c9691838aa400995a4664be004b0aee02c8509c7))

### Chores

- **evaluation**: Restructure evalation folder
  ([`3b2c4f1`](https://github.com/johannehouweling/ToxTempAssistant/commit/3b2c4f14e252d896d79b3961b3a96daab7c57ff8))

- **gitignore**: Added .venv
  ([`4129966`](https://github.com/johannehouweling/ToxTempAssistant/commit/41299667360fcdf005caeadbbdfed06ae59d3a2d))

### Documentation

- **README**: Update terminology and improve clarity in evaluation pipeline documentation
  ([`800b2e3`](https://github.com/johannehouweling/ToxTempAssistant/commit/800b2e32a7115b5d76edc27f2224c16b9ae34aa1))

### Features

- **ci**: Add GitHub Actions workflow for Docker cleanup
  ([`4668ef3`](https://github.com/johannehouweling/ToxTempAssistant/commit/4668ef3f82bcece91965b3047373975aa76e07f6))

- **ci**: Added manual action to investigate space issues on runner
  ([`bf53812`](https://github.com/johannehouweling/ToxTempAssistant/commit/bf538122ce0b275768816c05af93c03bb68e67bc))

- **evaluation**: Enable per-experiment image extraction and update configurations
  ([`e1c16c7`](https://github.com/johannehouweling/ToxTempAssistant/commit/e1c16c7873d445bd721247376ebdfcf5e67f3b4b))

- **image**: Add minimum dimensions for image processing to filter out artifacts
  ([`099fea9`](https://github.com/johannehouweling/ToxTempAssistant/commit/099fea9202cfaea355313a195e596b0f6c4c4fc4))

- **image**: Convert incompatible images to webp
  ([`bf0f729`](https://github.com/johannehouweling/ToxTempAssistant/commit/bf0f7296918bdff46d9d78405b8f57ccfbb04b8b))

### Refactoring

- **ci**: Change file ending
  ([`03495e7`](https://github.com/johannehouweling/ToxTempAssistant/commit/03495e7e15655d74d071e2ad3f37c498953f8fb7))

- **ci**: Improve cleanup.yml with diagnostics and pruning
  ([`347a794`](https://github.com/johannehouweling/ToxTempAssistant/commit/347a794eccee1a7df9d3c1709e515c712b4bbf6c))

- **ci**: Put bertscore in speerate eval block for poetry
  ([`6fff216`](https://github.com/johannehouweling/ToxTempAssistant/commit/6fff21639fc2019e7d2807ec2e70fe3ae99e8931))

- **config**: Import default prompts from AppConfig for consistency
  ([`48b285f`](https://github.com/johannehouweling/ToxTempAssistant/commit/48b285f6deccbbb8124a43127bcf48a9345827fa))

- **data_analysis**: Streamline imports and adjust base directory paths in summary scripts
  ([`4c7e23c`](https://github.com/johannehouweling/ToxTempAssistant/commit/4c7e23cae1424b51f695f1a0e8c7b0a262b6b1aa))

- **evaluation**: Enhance experiment configuration summary and streamline output handling
  ([`8cdd5d5`](https://github.com/johannehouweling/ToxTempAssistant/commit/8cdd5d5feb6a17dcee99d3a72994ce344cfc0183))

- **evaluation**: Fixed run_evals cli
  ([`09f8406`](https://github.com/johannehouweling/ToxTempAssistant/commit/09f8406fe85ec023145e4b501cbf63413f6f18cc))

- **evaluation**: Refactor evaluation pipelines and management commands
  ([`1980035`](https://github.com/johannehouweling/ToxTempAssistant/commit/198003581d735627a351b71cb4d9ec026a268327))

- **evaluation**: Restructures folder structure and made run_eval a manage.py command. Provided raw
  .pdf input files via git lfs.
  ([`782c5de`](https://github.com/johannehouweling/ToxTempAssistant/commit/782c5de050ec7fce090688620925c823a662bcdc))

- **tests**: Update image handling in PDF and DOCX tests to use actualy small PNG image
  ([`ccd2d81`](https://github.com/johannehouweling/ToxTempAssistant/commit/ccd2d8147ad0bbbba50b22c63a47ed9524ed3439))


## v2.1.0 (2025-10-27)

### Features

- **goatcounter**: Add GoatCounter to toxtempassistant.vhp4safety.nl
  ([`fe2a52e`](https://github.com/johannehouweling/ToxTempAssistant/commit/fe2a52e8e18d212bba55b50536bf1e8cb9fffba6))


## v2.0.1 (2025-10-26)

### Bug Fixes

- **login**: Addd references to the intro text
  ([`8486d1b`](https://github.com/johannehouweling/ToxTempAssistant/commit/8486d1b54f6b712a28ce302a9e2e2e3a7283da9c))


## v2.0.0 (2025-10-26)

### Bug Fixes

- **debug**: For debug session make autogenerated templates optional
  ([`95f7475`](https://github.com/johannehouweling/ToxTempAssistant/commit/95f7475822d0765b289a9f3b12b8d847519d1ce7))

- **demo**: Users now get warnign when trying to overwrite the demo assay
  ([`f1405fd`](https://github.com/johannehouweling/ToxTempAssistant/commit/f1405fde5480ac46e181b1bd1fd9042783fb8852))

- **llm**: Fix single question answer.
  ([`715511d`](https://github.com/johannehouweling/ToxTempAssistant/commit/715511dcf323c3efb05307d13451154b01458ede))

- **new**: File select disabled now corrected
  ([`c54a29d`](https://github.com/johannehouweling/ToxTempAssistant/commit/c54a29da80da1e78ed2394c7a3fdf23ee3749de6))

- **overwrite**: Overwriting an existing assay is fixed
  ([`7b698e9`](https://github.com/johannehouweling/ToxTempAssistant/commit/7b698e945d1c2ab648de28e90b07b8d3439aad1d))

- **ui**: Reduce top space
  ([`a653c6d`](https://github.com/johannehouweling/ToxTempAssistant/commit/a653c6d8292ecb3d126e4725338750a26d54c36e))

### Features

- **answerupdate**: Also allow users to extract images from pdf for single answer updates
  ([`cb30eb0`](https://github.com/johannehouweling/ToxTempAssistant/commit/cb30eb0cd5c60482e4a00a73892d58781e6c3db8))

- **demo**: Read-only demo assays for new users
  ([`4f5649e`](https://github.com/johannehouweling/ToxTempAssistant/commit/4f5649e58c65d817cbad1488ccd952fcdfaf5545))

- **llm**: Asynchronous llm everywhere
  ([`c8cf162`](https://github.com/johannehouweling/ToxTempAssistant/commit/c8cf1628c0661b7985d26bc78350916d8c941ea1))

- **llm**: Extracts now images from the pdfs and use them as context for answering the toxtemp
  questions
  ([`92b6b45`](https://github.com/johannehouweling/ToxTempAssistant/commit/92b6b45e6261ac0ea3034469381f55728ad0312d))

- **overview**: Pagination also suitable for mobile 3 intermediates only
  ([`1339c04`](https://github.com/johannehouweling/ToxTempAssistant/commit/1339c04d814bc32148ffc721854a07ba56b4d756))

### Refactoring

- **oboarding**: Improve texts
  ([`d0b6729`](https://github.com/johannehouweling/ToxTempAssistant/commit/d0b67298fbc6efb27b9fa22a53b6db34890b4156))


## v1.25.1 (2025-10-23)

### Bug Fixes

- **overview**: Actually accessing the owner of the assay/investigation in the overview for admins.
  ([`fc8dfc7`](https://github.com/johannehouweling/ToxTempAssistant/commit/fc8dfc7b01fa324231e55d4ff5cfb806d959c55f))


## v1.25.0 (2025-10-23)

### Features

- **overview**: Show owner of toxtemp for superusers
  ([`8a5275b`](https://github.com/johannehouweling/ToxTempAssistant/commit/8a5275b170ad29e8063cf6d6faee2db0e833e720))


## v1.24.0 (2025-10-22)

### Bug Fixes

- **base**: Fixed padding on all pages
  ([`8d3f289`](https://github.com/johannehouweling/ToxTempAssistant/commit/8d3f2890800aa2269b66342cf682a75caf744bab))

- **beta**: Allow admins to bypass the beta sdmission check
  ([`15d7874`](https://github.com/johannehouweling/ToxTempAssistant/commit/15d787401fcf899c0ce7bec8e307ef96c8ffa35d))

- **onboardig**: Fixed automated onboarding, changed start.html to overview.html
  ([`530e81f`](https://github.com/johannehouweling/ToxTempAssistant/commit/530e81f0395f9b3c85dc1f4e0552f2e097cd4a19))

- **onboarding**: Fix placement of the toast
  ([`3380552`](https://github.com/johannehouweling/ToxTempAssistant/commit/3380552a11da4a0ae099932dfc119744c80ad373))

- **onboarding**: Fixed text
  ([`7a43983`](https://github.com/johannehouweling/ToxTempAssistant/commit/7a439833dcf7bb2c27c3596370da5a1b0bdc6e14))

- **onboarding**: Posiitioning left toast
  ([`4c01463`](https://github.com/johannehouweling/ToxTempAssistant/commit/4c01463a4ebedd9243e474f2465aae78a970216f))

- **onboarding**: Refined onboarding texts
  ([`5e1c0bf`](https://github.com/johannehouweling/ToxTempAssistant/commit/5e1c0bf04cd954ee45ef6a64007621f8e27a1ace))

### Features

- **onboarding**: Add multi-page guided tour with circular navigation
  ([`50f5178`](https://github.com/johannehouweling/ToxTempAssistant/commit/50f5178285ae59aa1c755dbfc33927c88a94e905))

- **onboarding**: Add previous button
  ([`0c91b76`](https://github.com/johannehouweling/ToxTempAssistant/commit/0c91b76302fd113b1d26d92174ca0e84668263a4))

- **onboarding**: Added onboarding for the answer page
  ([`8a6448c`](https://github.com/johannehouweling/ToxTempAssistant/commit/8a6448c2d1b404b543e6fc361f8587ae9dc52f4f))

- **toast**: Improved styling
  ([`5776472`](https://github.com/johannehouweling/ToxTempAssistant/commit/5776472724121f3c6150e407143de2c65b391d44))

### Refactoring

- **onboarding**: Changed back to dynamic placement of the toast
  ([`bd15a87`](https://github.com/johannehouweling/ToxTempAssistant/commit/bd15a8773c5dcd0b4b3cf5dabc19dada97921f98))

- **onboarding**: Clean up debug messages
  ([`c52fbf8`](https://github.com/johannehouweling/ToxTempAssistant/commit/c52fbf84bc997a433b14e2c163fd882017feb92d))

- **onboarding**: Fixed the toast to the center, added global step counter and made tied the
  individual tours together in one global tour
  ([`1dbe56e`](https://github.com/johannehouweling/ToxTempAssistant/commit/1dbe56eda14e77478b4d99fc69ea71daa21d6670))


## v1.23.0 (2025-10-01)

### Bug Fixes

- **ci**: Typo
  ([`ea41d91`](https://github.com/johannehouweling/ToxTempAssistant/commit/ea41d9160fc226793a22d678fec37e3848224411))

- **login**: Adjust badge position for sign-up button in login form
  ([`326d2f9`](https://github.com/johannehouweling/ToxTempAssistant/commit/326d2f94db7faab36212e5e2a35a95ddf8560c42))

### Features

- **login**: Replace static logos with a carousel for supported institutions
  ([`9789be4`](https://github.com/johannehouweling/ToxTempAssistant/commit/9789be4deb84294863f7970527ca1646ec3d3b71))

- **login**: Update supported by section with institutional logos and remove feature list
  ([`b68de9f`](https://github.com/johannehouweling/ToxTempAssistant/commit/b68de9fa68306dd138364ab7b3040ad9ee82d6dc))

- **ui**: Added parnter logos
  ([`4997516`](https://github.com/johannehouweling/ToxTempAssistant/commit/49975165619b428d1bbf0716baaa81b792f64a50))


## v1.22.0 (2025-10-01)

### Bug Fixes

- **tests**: Introduce AdminFactory and refactor beta tests to use factories
  ([`ba75d05`](https://github.com/johannehouweling/ToxTempAssistant/commit/ba75d05299f6439f1eb57844ed428f490b289094))


## v1.21.3 (2025-09-25)

### Bug Fixes

- **ui**: Zenodo auto resolve link to most recent version
  ([`c538224`](https://github.com/johannehouweling/ToxTempAssistant/commit/c538224c1f8e7100ebedd73306faca54aee6836f))


## v1.21.2 (2025-09-25)

### Bug Fixes

- **ui**: Add zenodo doi link to config
  ([`3825ef3`](https://github.com/johannehouweling/ToxTempAssistant/commit/3825ef36467c33d32638402e9b6f90623f9de9d2))

- **ui**: Change link and svg to zenodo archive which points to newest version also on offcanvas
  ([`13e2fbf`](https://github.com/johannehouweling/ToxTempAssistant/commit/13e2fbf9fa4c4d48ccb5e851200e1c35a92803d8))


## v1.21.1 (2025-09-25)

### Bug Fixes

- **config**: Changed zenodo link to doi which refers always to the newest version
  ([`ab6a13c`](https://github.com/johannehouweling/ToxTempAssistant/commit/ab6a13cecad3a710cd32030122db197056334fbf))


## v1.21.0 (2025-09-22)

### Features

- **login**: Update description of ToxTempAssistant to clarify functionality and benefits
  ([`bbeca8d`](https://github.com/johannehouweling/ToxTempAssistant/commit/bbeca8d938c626cc605f54e664738f6f01f47f0e))


## v1.20.0 (2025-09-17)

### Features

- **email**: Add email configuration and send_email_task for sending emails (gmail for now)
  ([`adc7be8`](https://github.com/johannehouweling/ToxTempAssistant/commit/adc7be8c213dc2e93bef48b36da5bd1cfa71a527))


## v1.19.2 (2025-09-16)

### Bug Fixes

- **ui**: Export buttons on overview work now as expected
  ([`574370f`](https://github.com/johannehouweling/ToxTempAssistant/commit/574370fe4ad3894e114f32d5500513bbadd44685))


## v1.19.1 (2025-09-16)


## v1.19.0 (2025-09-16)

### Features

- **ui**: Login screen with explanation.
  ([`a3d7040`](https://github.com/johannehouweling/ToxTempAssistant/commit/a3d7040f827d1b95c158f6f5b8763237514a407c))


## v1.18.0 (2025-09-16)

### Features

- **export**: Add allowed export types to Config for improved validation
  ([`0a6cfe2`](https://github.com/johannehouweling/ToxTempAssistant/commit/0a6cfe25f378fe36d9d2c4df726e2a840b689bbd))


## v1.17.0 (2025-09-16)

### Features

- **db**: Added preferences field for Person
  ([#57](https://github.com/johannehouweling/ToxTempAssistant/pull/57),
  [`cd29891`](https://github.com/johannehouweling/ToxTempAssistant/commit/cd29891fea6aa972a463e0e2509f5366ee8c0043))

- **ui**: Add current URL context processor and update templates for improved navigation and
  onboarding ([#57](https://github.com/johannehouweling/ToxTempAssistant/pull/57),
  [`cd29891`](https://github.com/johannehouweling/ToxTempAssistant/commit/cd29891fea6aa972a463e0e2509f5366ee8c0043))

- **ui**: Show onboarding on first login
  ([#57](https://github.com/johannehouweling/ToxTempAssistant/pull/57),
  [`cd29891`](https://github.com/johannehouweling/ToxTempAssistant/commit/cd29891fea6aa972a463e0e2509f5366ee8c0043))


## v1.16.0 (2025-09-16)

### Bug Fixes

- **ui**: Rename create button to more generic save (because it also doubles as modify button in the
  respective context)
  ([`0196129`](https://github.com/johannehouweling/ToxTempAssistant/commit/01961297cf0dc4b65a646d6a632baf0ade3cfe85))

### Features

- **ui**: Overview buttons responsive with icons and text
  ([`99ea8a4`](https://github.com/johannehouweling/ToxTempAssistant/commit/99ea8a4133381cbb36c105f7c886e9d93964d77a))


## v1.15.1 (2025-09-16)

### Bug Fixes

- **ui**: Guided/help tour button
  ([`e1d2c51`](https://github.com/johannehouweling/ToxTempAssistant/commit/e1d2c51951745ba95c34615eb9ba61ca8b6a946e))


## v1.15.0 (2025-09-15)

### Bug Fixes

- **ui**: Allow upload dcument when new created assay
  ([#55](https://github.com/johannehouweling/ToxTempAssistant/pull/55),
  [`b2844c1`](https://github.com/johannehouweling/ToxTempAssistant/commit/b2844c124042e23d7cdf52ec5cc7d6d00e5be459))

### Features

- **ui**: ToxTempAssistant headline becomes link to return to main page.
  ([#55](https://github.com/johannehouweling/ToxTempAssistant/pull/55),
  [`b2844c1`](https://github.com/johannehouweling/ToxTempAssistant/commit/b2844c124042e23d7cdf52ec5cc7d6d00e5be459))


## v1.14.0 (2025-09-15)

### Bug Fixes

- **ui**: 1/n files on upload bar
  ([`9cd52f4`](https://github.com/johannehouweling/ToxTempAssistant/commit/9cd52f44de2d431af1d703a245a8fc5b4bcfbbd1))

- **ui**: Logic when to allow file-upload improved.
  ([`9cd52f4`](https://github.com/johannehouweling/ToxTempAssistant/commit/9cd52f44de2d431af1d703a245a8fc5b4bcfbbd1))

- **ui**: Offcanvas now compatible with additional modals
  ([`9cd52f4`](https://github.com/johannehouweling/ToxTempAssistant/commit/9cd52f44de2d431af1d703a245a8fc5b4bcfbbd1))

- **ui**: Upload bar
  ([`9cd52f4`](https://github.com/johannehouweling/ToxTempAssistant/commit/9cd52f44de2d431af1d703a245a8fc5b4bcfbbd1))

- **upload**: Handling of tempfiles
  ([`9cd52f4`](https://github.com/johannehouweling/ToxTempAssistant/commit/9cd52f44de2d431af1d703a245a8fc5b4bcfbbd1))

### Features

- **ui**: Progressbar during upload
  ([`9cd52f4`](https://github.com/johannehouweling/ToxTempAssistant/commit/9cd52f44de2d431af1d703a245a8fc5b4bcfbbd1))

- **ui**: User Interface Improvements
  ([`9cd52f4`](https://github.com/johannehouweling/ToxTempAssistant/commit/9cd52f44de2d431af1d703a245a8fc5b4bcfbbd1))


## v1.13.0 (2025-09-15)

### Features

- **ui**: Added favicon
  ([`33f54d0`](https://github.com/johannehouweling/ToxTempAssistant/commit/33f54d03454e5fd1d9996e76e8f5a17223a566a1))


## v1.12.1 (2025-09-15)

### Bug Fixes

- **ui**: Margin for toxtempassistant logo match margin of user-menu
  ([`b551fb2`](https://github.com/johannehouweling/ToxTempAssistant/commit/b551fb208b3e2c005e91a1d94b8f4a572c54bf38))

- **ui**: Padding smaller
  ([`617e09d`](https://github.com/johannehouweling/ToxTempAssistant/commit/617e09df2c961c20702a3019f0c19195deb39f0b))


## v1.12.0 (2025-09-14)

### Features

- **ui**: Optimizations for small screens (responsiveness)
  ([`5b15096`](https://github.com/johannehouweling/ToxTempAssistant/commit/5b1509611ffe6111791d3eb2009d1f98c5507f5f))


## v1.11.2 (2025-09-14)


## v1.11.1 (2025-09-14)


## v1.11.0 (2025-09-14)

### Bug Fixes

- **ui**: Switched position of last_changed
  ([`a51c953`](https://github.com/johannehouweling/ToxTempAssistant/commit/a51c9536a30e6879b26c61c2dcf6984f280bb25d))

### Features

- **ci**: Include TAG and version in deploy and frontend
  ([`5e34dab`](https://github.com/johannehouweling/ToxTempAssistant/commit/5e34dab37e7882b74ce16ff9a0e026397e6b7a5f))


## v1.10.0 (2025-09-14)


## v1.9.1 (2025-09-14)

### Bug Fixes

- **export**: Update version and references in configuration; improve export filename and metadata
  ([`e530a43`](https://github.com/johannehouweling/ToxTempAssistant/commit/e530a4310cf0d01d4072afeb80e664d93f1a9748))


## v1.9.0 (2025-09-14)


## v1.8.1 (2025-09-14)

### Bug Fixes

- **logic**: Hide Assays that don't have a question_set attached to them
  ([#49](https://github.com/johannehouweling/ToxTempAssistant/pull/49),
  [`c98318a`](https://github.com/johannehouweling/ToxTempAssistant/commit/c98318a683216f2b8d8c83c7337beccb933a77f4))

- **ui**: Allow interaction with busy and scheduled buttons for tooltip toggling
  ([#49](https://github.com/johannehouweling/ToxTempAssistant/pull/49),
  [`c98318a`](https://github.com/johannehouweling/ToxTempAssistant/commit/c98318a683216f2b8d8c83c7337beccb933a77f4))

- **ui**: Distinguish assays by submission_data string in case of identical title
  ([#49](https://github.com/johannehouweling/ToxTempAssistant/pull/49),
  [`c98318a`](https://github.com/johannehouweling/ToxTempAssistant/commit/c98318a683216f2b8d8c83c7337beccb933a77f4))

- **ui**: Fix ui buttons in overview don't align up with delete.
  ([#49](https://github.com/johannehouweling/ToxTempAssistant/pull/49),
  [`c98318a`](https://github.com/johannehouweling/ToxTempAssistant/commit/c98318a683216f2b8d8c83c7337beccb933a77f4))

- **ui**: Protect overwriting existing ToxTemp, with overwrite checkbox
  ([#49](https://github.com/johannehouweling/ToxTempAssistant/pull/49),
  [`c98318a`](https://github.com/johannehouweling/ToxTempAssistant/commit/c98318a683216f2b8d8c83c7337beccb933a77f4))

### Refactoring

- **ui**: Added function description
  ([#49](https://github.com/johannehouweling/ToxTempAssistant/pull/49),
  [`c98318a`](https://github.com/johannehouweling/ToxTempAssistant/commit/c98318a683216f2b8d8c83c7337beccb933a77f4))


## v1.8.0 (2025-09-14)

### Features

- **config**: Increase max_size_mb from 20 to 30
  ([`e4d7c23`](https://github.com/johannehouweling/ToxTempAssistant/commit/e4d7c23a50df4cd01aee707ffe89c72a384c4855))

- **ui**: Add source parameter to delete_assay for better navigation
  ([`34a1034`](https://github.com/johannehouweling/ToxTempAssistant/commit/34a1034897204af807f377484acc5a968dd2e4bf))

### Refactoring

- Delete upload
  ([`6502595`](https://github.com/johannehouweling/ToxTempAssistant/commit/65025957f228be53920857fccc34fdaf3a736f27))

### Testing

- Add tests for AssayAnswerForm file upload handling
  ([`1d776e0`](https://github.com/johannehouweling/ToxTempAssistant/commit/1d776e04a2c27fa47bd877d8e480062f7c2dc146))


## v1.7.0 (2025-09-13)

### Bug Fixes

- **llm**: Allow missing OpenAI API key during testing
  ([`eec94ec`](https://github.com/johannehouweling/ToxTempAssistant/commit/eec94ec50973df064ac9681568296584fded9d66))

### Code Style

- **llm**: Format docstring for get_llm function for better readability
  ([`f5b837d`](https://github.com/johannehouweling/ToxTempAssistant/commit/f5b837d5fd434311b08a594a32d6bb6b19cb9726))

### Features

- **migrations**: Add new fields and alter existing fields in assay and questionset models
  ([`6a0df8c`](https://github.com/johannehouweling/ToxTempAssistant/commit/6a0df8c7e1e81230baa73b58203a6c8dd23ad7d9))

- **test**: Add comprehensive tests for DocumentDictFactory and process_llm_async functionality
  ([`f8cfafa`](https://github.com/johannehouweling/ToxTempAssistant/commit/f8cfafaae7ad4c5819462e022beca48d227e6f40))


## v1.6.3 (2025-09-13)

### Bug Fixes

- **ci**: PAT Token
  ([`29ae57f`](https://github.com/johannehouweling/ToxTempAssistant/commit/29ae57fc11ae1a41ecea0bfcdb3a63e7c2bc7b13))

- **ci**: Persists crentials and bump actions
  ([`a06d6f6`](https://github.com/johannehouweling/ToxTempAssistant/commit/a06d6f631001f3dfcd2d69d4b5f93a73edc3589c))

- **ci**: Reverse to PATOKEN [skip ci]
  ([`d3e5f23`](https://github.com/johannehouweling/ToxTempAssistant/commit/d3e5f23c53cb615963012ec9c50ce1a4df5c64aa))

- **ci**: Token
  ([`f4a4d7d`](https://github.com/johannehouweling/ToxTempAssistant/commit/f4a4d7d361d8c3eec0798ad9ca5f5e3a7e0d92da))

### Chores

- **ci**: Trigger build
  ([`73008af`](https://github.com/johannehouweling/ToxTempAssistant/commit/73008af5c3a08ad1394f9dd5829b46847f1fa7d2))


## v1.6.2 (2025-09-13)

### Bug Fixes

- **ci**: Change to hybrid GITHUB/PAT Token
  ([`e6b49ff`](https://github.com/johannehouweling/ToxTempAssistant/commit/e6b49ffa674d3ce4f7915d0b2035b315014a370b))


## v1.6.1 (2025-09-13)

### Bug Fixes

- **ci**: Change to github token for authentication
  ([`0ec61e4`](https://github.com/johannehouweling/ToxTempAssistant/commit/0ec61e4e683bfccf17eaa1e3934db54707d965e8))


## v1.6.0 (2025-09-13)

### Features

- **question**: Git commit -m ' update UI logic for new question set displa_
  ([#31](https://github.com/johannehouweling/ToxTempAssistant/pull/31),
  [`5f12fd7`](https://github.com/johannehouweling/ToxTempAssistant/commit/5f12fd78683fc486b6d9688ffce776e1fe1b6fe8))

- **question**: Update UI logic for new question set
  ([#31](https://github.com/johannehouweling/ToxTempAssistant/pull/31),
  [`5f12fd7`](https://github.com/johannehouweling/ToxTempAssistant/commit/5f12fd78683fc486b6d9688ffce776e1fe1b6fe8))

- **validation**: Enhance CSV generation with model name and overwrite option
  ([#31](https://github.com/johannehouweling/ToxTempAssistant/pull/31),
  [`5f12fd7`](https://github.com/johannehouweling/ToxTempAssistant/commit/5f12fd78683fc486b6d9688ffce776e1fe1b6fe8))


## v1.5.0 (2025-09-12)

### Chores

- Update dependencies ([#32](https://github.com/johannehouweling/ToxTempAssistant/pull/32),
  [`edfbd32`](https://github.com/johannehouweling/ToxTempAssistant/commit/edfbd32a212ae6fb67ba702235b9a82e874bfdc9))

### Documentation

- Update toc ([#32](https://github.com/johannehouweling/ToxTempAssistant/pull/32),
  [`edfbd32`](https://github.com/johannehouweling/ToxTempAssistant/commit/edfbd32a212ae6fb67ba702235b9a82e874bfdc9))

### Features

- Improve error reporting ([#32](https://github.com/johannehouweling/ToxTempAssistant/pull/32),
  [`edfbd32`](https://github.com/johannehouweling/ToxTempAssistant/commit/edfbd32a212ae6fb67ba702235b9a82e874bfdc9))

- **debug**: Add INFO list of ENV variables
  ([#32](https://github.com/johannehouweling/ToxTempAssistant/pull/32),
  [`edfbd32`](https://github.com/johannehouweling/ToxTempAssistant/commit/edfbd32a212ae6fb67ba702235b9a82e874bfdc9))

- **housekeeping**: Installed pre-commit, setup ruff as lint and fixed all ruff linting errors.
  ([#32](https://github.com/johannehouweling/ToxTempAssistant/pull/32),
  [`edfbd32`](https://github.com/johannehouweling/ToxTempAssistant/commit/edfbd32a212ae6fb67ba702235b9a82e874bfdc9))

- **llm**: More robust error reporting
  ([#32](https://github.com/johannehouweling/ToxTempAssistant/pull/32),
  [`edfbd32`](https://github.com/johannehouweling/ToxTempAssistant/commit/edfbd32a212ae6fb67ba702235b9a82e874bfdc9))

### Refactoring

- Update folder structure ([#32](https://github.com/johannehouweling/ToxTempAssistant/pull/32),
  [`edfbd32`](https://github.com/johannehouweling/ToxTempAssistant/commit/edfbd32a212ae6fb67ba702235b9a82e874bfdc9))


## v1.4.0 (2025-08-12)

### Features

- **housekeeping**: Installed pre-commit, setup ruff as lint and fixed all ruff linting errors.
  ([`91391cc`](https://github.com/johannehouweling/ToxTempAssistant/commit/91391ccda4ae6f5961629726d1a6495a604d700f))


## v1.3.1 (2025-08-05)

### Bug Fixes

- **validation**: Update model imports and improve zip formatting in validation pipelines
  ([`c1236f7`](https://github.com/johannehouweling/ToxTempAssistant/commit/c1236f70506ba0824a7fdc6f20486dd8e793a154))


## v1.3.0 (2025-08-04)

### Bug Fixes

- **ci**: Attach docker default network
  ([`3977fef`](https://github.com/johannehouweling/ToxTempAssistant/commit/3977fef5d811be4a3c0a79c7b037b40ae1f95327))

- **ci**: Poetry run gunicorn
  ([`b4446aa`](https://github.com/johannehouweling/ToxTempAssistant/commit/b4446aa49efd4e050e3ab9af9c122ae654e74060))

- **ci**: QuestionSet model has Person as optional ForeignKey
  ([`5d3cd96`](https://github.com/johannehouweling/ToxTempAssistant/commit/5d3cd96106281197c26847da59643103db893cd6))

### Features

- **questionset**: Add visibility control
  ([#27](https://github.com/johannehouweling/ToxTempAssistant/pull/27),
  [`a8a5939`](https://github.com/johannehouweling/ToxTempAssistant/commit/a8a5939bcfac02b2ee3a3e91ab8639658095eec6))


## v1.2.2 (2025-08-01)

### Bug Fixes

- **ci**: TESTING bool from string
  ([`b3da36e`](https://github.com/johannehouweling/ToxTempAssistant/commit/b3da36ee6bf93082d2e5f35801ea3e629ba89871))


## v1.2.1 (2025-08-01)

### Bug Fixes

- **ci**: Preserve sudo env
  ([`5efbeb1`](https://github.com/johannehouweling/ToxTempAssistant/commit/5efbeb1a581b1119ae5053e074804c196fc67444))


## v1.2.0 (2025-08-01)

### Features

- **ci**: Only run release when pushing to main and then deploy
  ([`c4f275b`](https://github.com/johannehouweling/ToxTempAssistant/commit/c4f275bd5aaba4b9083f2905aa14a5ca416e7b83))


## v1.1.0 (2025-08-01)

### Bug Fixes

- **ci**: Add gha cache
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Allow pip to use system python in docker
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Buildx args
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Fancy reports
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Give postgres a chance to stop gracefully
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Only release on push to main
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Other fancy reporting tool
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Poetry inside venv
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Return to original fancy report
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Revert to docker compose
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Switch from python to docker
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Switch to docker compose
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Try isolate test, so can exit w code0
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Upload test-results
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Use buildx
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Use buildx fix env
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Use virtualenv by default
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Add testing echo
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Build for testing
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Export test-results / named folder in docker
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Forward build env to djangoapp env for TESTING
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Include dev packages for poetry install when under TESTING
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Poetry install
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Poetry only install extra for testing in docker
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Poetry without dev in production
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Position of --profile flag
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Remove depends on, to use profiles more flexibly
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Return to nc instead of pg_isready
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Set DJANGO_SETTINGS for pytest
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Set DJANGO_SETTINGS for pytest - correction
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Set TESTING ENV in docker build process
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **settings**: Testing flag wrong
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **testing**: Djangoq not during testing
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **testing**: Djangoq not during testing 2
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **testing**: Pause testing for investigations
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **testing**: Toggle .env.dummy also in docker-compose
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **testing**: Use .env.dummy for testing
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

### Chores

- **debug**: Django startup/test
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **debug**: Dont report missing API Key as error during TESTING
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **testing**: Added __init__
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

### Documentation

- Add content on testing
  ([`bdf9294`](https://github.com/johannehouweling/ToxTempAssistant/commit/bdf92948b1cf5731b4a7df1b623afe334519c61a))

- Added stub on testing
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

### Features

- **ci**: Added github actions to test, release and deploy automatically
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Coverage report
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **ci**: Integrate testing to github_actions
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Add profiles for test and prod
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **testing**: Added first dummy test
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **testing**: Added new env variables for test-db
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **testing**: Load testing db in settings for tests
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **testing**: Testing inside docker
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

### Refactoring

- Move all tests to dedicated folder
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **depreciation**: __ instead of . for accessing model attributes
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Postgres port
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **docker**: Reintroduced depends on
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))

- **settings**: Simplify POSTGRES setup
  ([`349aec9`](https://github.com/johannehouweling/ToxTempAssistant/commit/349aec9361f15d16fc066e497e3441a77c06a57b))


## v1.0.11 (2025-07-30)

### Bug Fixes

- **ci**: Switch to PA TOKEN
  ([`407424c`](https://github.com/johannehouweling/ToxTempAssistant/commit/407424c97bdbf31c1995ea56f6fa9455e7b9fd5c))


## v1.0.10 (2025-07-30)

### Bug Fixes

- **ci**: Switch to PA TOKEN
  ([`d46fa92`](https://github.com/johannehouweling/ToxTempAssistant/commit/d46fa92637c374747bca25a56570ec330f334fc1))


## v1.0.9 (2025-07-30)

### Bug Fixes

- **ci**: Keep credentials
  ([`086e73f`](https://github.com/johannehouweling/ToxTempAssistant/commit/086e73f2a50002d078dc48f92864752c039ef505))

- **ci**: Switch back to token
  ([`c4bbeda`](https://github.com/johannehouweling/ToxTempAssistant/commit/c4bbeda22492e61681ffe3965c699e8a3bfbdf21))

- **ci**: Use token
  ([`5829818`](https://github.com/johannehouweling/ToxTempAssistant/commit/58298180a88265b89e99f20839ad7aa233fb6814))

- **ci**: Use token
  ([`8eae80e`](https://github.com/johannehouweling/ToxTempAssistant/commit/8eae80e7aff32824c0c49d693ce01c21ba92e50f))

- **ci**: Use token test GITHUBTOKEN
  ([`db53ce2`](https://github.com/johannehouweling/ToxTempAssistant/commit/db53ce209e4d2e39c752330021947d0192dd5198))

- **docker**: Install poetry when in home dir
  ([`b2a716d`](https://github.com/johannehouweling/ToxTempAssistant/commit/b2a716d100838a5abc7185ffd5d4735e125975f3))


## v1.0.8 (2025-07-30)

### Bug Fixes

- **docker**: Pipx install
  ([`c22df79`](https://github.com/johannehouweling/ToxTempAssistant/commit/c22df79f70f7fcd04241238562771772b8703882))


## v1.0.7 (2025-07-30)

### Bug Fixes

- **docker**: Poetry install
  ([`8fcf54a`](https://github.com/johannehouweling/ToxTempAssistant/commit/8fcf54ad640147c4ac5e368136e2bea174ed3cd1))


## v1.0.6 (2025-07-30)

### Bug Fixes

- **docker**: Poerty install
  ([`50efa47`](https://github.com/johannehouweling/ToxTempAssistant/commit/50efa473b64b6455b8508623e64c69cf1621729d))


## v1.0.5 (2025-07-30)

### Bug Fixes

- **model**: Make created_by optional for question_set
  ([`ed1e326`](https://github.com/johannehouweling/ToxTempAssistant/commit/ed1e326b58866869136ad007bbd0c1531ac7acab))


## v1.0.4 (2025-07-30)

### Bug Fixes

- **ci**: Removed TAG env from docker
  ([`2319d5c`](https://github.com/johannehouweling/ToxTempAssistant/commit/2319d5c5cd30058945060e4d7b9a7573ddd0629f))


## v1.0.3 (2025-07-30)


## v1.0.2 (2025-07-30)

### Bug Fixes

- **ci**: Debug
  ([`073de1c`](https://github.com/johannehouweling/ToxTempAssistant/commit/073de1c0e7456973b4fa0a45f31949e36812d60b))

- **ci**: Debug
  ([`29bc074`](https://github.com/johannehouweling/ToxTempAssistant/commit/29bc07487b6596fadfb3ebc1177d15149a14d323))

- **ci**: Debug sshkey for semantic release
  ([`19d4f72`](https://github.com/johannehouweling/ToxTempAssistant/commit/19d4f72e082cecc26ba7da98c564d891e03a10f4))

- **ci**: Push version in ssh-agent
  ([`d0e5d00`](https://github.com/johannehouweling/ToxTempAssistant/commit/d0e5d00d2d3688667e82d5b12c56ebacba1c49ad))

- **ci**: Try again with ssh
  ([`579a2b3`](https://github.com/johannehouweling/ToxTempAssistant/commit/579a2b31aa7efbeae45096fb1b87ae1aa0fd1533))

- **ci**: Type github
  ([`3c6a484`](https://github.com/johannehouweling/ToxTempAssistant/commit/3c6a4849f100598020ac06fad19ab48afbb8fb25))

- **ci**: Use consistent key
  ([`3efa239`](https://github.com/johannehouweling/ToxTempAssistant/commit/3efa239a2597ae40bdceab0c2fcad713c79aa395))

- **ci**: Use SSH instead of token
  ([`3b3674f`](https://github.com/johannehouweling/ToxTempAssistant/commit/3b3674ffde3a67ddafd8e3a6576679554021b4c8))


## v1.0.1 (2025-07-30)


## v1.0.0 (2025-07-30)

### Bug Fixes

- **ci**: 700 on .ssh permissions
  ([`71c0386`](https://github.com/johannehouweling/ToxTempAssistant/commit/71c038641a4f2c6ac26267b3f0eccb9aaaf2fff6))

- **ci**: Allow 0 major version for now
  ([`1d42322`](https://github.com/johannehouweling/ToxTempAssistant/commit/1d4232289e78d9163a7a84396a6f06e68407e21b))

- **ci**: Another try
  ([`c7c077e`](https://github.com/johannehouweling/ToxTempAssistant/commit/c7c077e434f43b4fbe55227873f7c94de09bad4c))

- **ci**: Bump semantic-release/git plugin version
  ([`59d6d40`](https://github.com/johannehouweling/ToxTempAssistant/commit/59d6d40575851df384b5e9733dd09c154ed46758))

- **ci**: Bump version of github actions
  ([`50faa90`](https://github.com/johannehouweling/ToxTempAssistant/commit/50faa90dc58623ba76b021b384e0ef8d8b959d99))

- **ci**: Change flag from --dev to --extras
  ([`90f6619`](https://github.com/johannehouweling/ToxTempAssistant/commit/90f6619106b1a8ac66837b684e404ff8a0e7fef4))

- **ci**: Debug release
  ([`179bbdf`](https://github.com/johannehouweling/ToxTempAssistant/commit/179bbdf0de377d39ddcba7d23eeabf9aafca4039))

- **ci**: Explicitly set write permissions for release workflow
  ([`55d88d2`](https://github.com/johannehouweling/ToxTempAssistant/commit/55d88d238ff1c51273bd14e023cc76c4b34ec851))

- **ci**: Fix config
  ([`0c151d5`](https://github.com/johannehouweling/ToxTempAssistant/commit/0c151d51c874c86f973aecf9dbe8e69a0941c9a3))

- **ci**: Inject the TOKEN explicitly
  ([`42fc204`](https://github.com/johannehouweling/ToxTempAssistant/commit/42fc20460d91b4cc991e46b0acf1e94105d159b2))

- **ci**: Install dev dependencies
  ([`f2e5a0c`](https://github.com/johannehouweling/ToxTempAssistant/commit/f2e5a0cac95b2f373f49aba6737d8bf7904c665c))

- **ci**: Reference TOKEN in pyproject
  ([`dc9eddb`](https://github.com/johannehouweling/ToxTempAssistant/commit/dc9eddbe3168f715c78566525f0beae747ba2140))

- **ci**: Remove verbose
  ([`4f62f07`](https://github.com/johannehouweling/ToxTempAssistant/commit/4f62f07968906e428b5693f6850d81a829f23f13))

- **ci**: Revert to action/setup-python
  ([`f5b3d30`](https://github.com/johannehouweling/ToxTempAssistant/commit/f5b3d30a82fc0016e8d862da43535f35bb1ada89))

- **ci**: Switch from publish to version
  ([`e7b5257`](https://github.com/johannehouweling/ToxTempAssistant/commit/e7b525792c0438a6e9a91b6d27eed4c77b437e06))

- **ci**: Toggle testing off
  ([`f87a261`](https://github.com/johannehouweling/ToxTempAssistant/commit/f87a2613b2ce2cd51fe199d421b120f65b6bdfd1))

- **ci**: Try action/semantic-release
  ([`af94168`](https://github.com/johannehouweling/ToxTempAssistant/commit/af941687b329a97c32801ff7dbdc2b1c99e8998a))

- **docs**: Small updates
  ([`db60958`](https://github.com/johannehouweling/ToxTempAssistant/commit/db609586fe269f6a581f3f240d7b1589dfc83641))

### Features

- **ci**: Added ci.yml to automate testing
  ([`6df91cd`](https://github.com/johannehouweling/ToxTempAssistant/commit/6df91cda9c301873fe66523ace1fa65aff5a0a0a))

- **package**: Switch requirements.txt to poetry
  ([`902c9ac`](https://github.com/johannehouweling/ToxTempAssistant/commit/902c9ac3ebf358d3263cf0407aee01d6780fb3ad))


## v0.9.1 (2025-06-18)


## v0.9.0 (2025-06-03)

- Initial Release
