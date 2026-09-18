KIT ?= ../../kit
SEALED ?= ../../kit-sealed
DAY6 ?= ../../kit-day6
SECRET ?=
REPORT ?=

.PHONY: kit sealed all clean clean-sealed check serve verify verify-sealed distribute serve-sealed score-sealed

# ---- participants -------------------------------------------------------------
# `all` builds ONLY the practice kit. The sealed day-6 corpus is an organiser step.
all: kit verify
kit: ; python3 generate.py --variant A --out $(KIT)
verify: ; python3 verify.py --kit $(KIT)
serve: ; python3 replay/serve.py --kit $(KIT) --port 8719
check: ; python3 score.py --report examples/loop-report.level3.json \
	--ground-truth $(KIT)/ground_truth/ground_truth.json
clean: ; rm -rf $(KIT)

# ---- organisers only ----------------------------------------------------------
# Build the sealed corpus from a passphrase only you hold. Run this on your own machine,
# any time before the event. The same passphrase always rebuilds the same corpus.
sealed:
	@test -n "$(SECRET)" || { \
	  echo 'make sealed needs a passphrase only the organisers hold:'; \
	  echo '    make sealed SECRET="a phrase you keep"'; \
	  echo 'Do not run this on a participant machine.'; exit 1; }
	python3 generate.py --sealed "$(SECRET)" --out $(SEALED)
verify-sealed: ; python3 verify.py --kit $(SEALED)
clean-sealed: ; rm -rf $(SEALED) $(DAY6) $(DAY6).zip

# What teams receive at 12:00 on day 6: the sealed kit WITHOUT its ground truth, as a zip.
distribute:
	@test -d $(SEALED)/ground_truth_SEALED || { echo 'no sealed kit — run make sealed first'; exit 1; }
	rm -rf $(DAY6) $(DAY6).zip
	mkdir -p $(DAY6)
	cp -R $(SEALED)/corpus $(SEALED)/corpus_sample $(SEALED)/labels \
	      $(SEALED)/catalog.json $(SEALED)/asks.md $(SEALED)/manifest.json $(DAY6)/
	cd $(dir $(abspath $(DAY6))) && zip -qr $(notdir $(abspath $(DAY6))).zip $(notdir $(abspath $(DAY6)))
	@echo "wrote $(DAY6).zip — contains NO ground truth. Hand this out."

# The replay endpoint teams verify fixes against on day 6. Runs on your machine only;
# teams get the URL, never the ground-truth file.
serve-sealed:
	python3 replay/serve.py --kit $(SEALED) \
	  --ground-truth $(SEALED)/ground_truth_SEALED/ground_truth.json --host 0.0.0.0 --port 8719

# Score one team's sealed report:  make score-sealed REPORT=path/to/loop-report.json
score-sealed:
	@test -n "$(REPORT)" || { echo 'make score-sealed REPORT=path/to/loop-report.json'; exit 1; }
	python3 score.py --report $(REPORT) --ground-truth $(SEALED)/ground_truth_SEALED/ground_truth.json

# ---- day-1 hand-out -------------------------------------------------------------
# Everything participants need on day 1: the tools directory and the generated practice
# kit (its ground truth included, on purpose), in the same relative layout the docs use.
BUNDLE ?= ../../nexus-loop-day1
bundle: kit
	rm -rf $(BUNDLE) $(BUNDLE).zip
	mkdir -p $(BUNDLE)/tools
	cp -R . $(BUNDLE)/tools/nexus-loop-kit
	cp -R $(KIT) $(BUNDLE)/kit
	find $(BUNDLE) -name __pycache__ -type d -exec rm -rf {} +
	cd $(dir $(abspath $(BUNDLE))) && zip -qr $(notdir $(abspath $(BUNDLE))).zip $(notdir $(abspath $(BUNDLE)))
	@echo "wrote $(BUNDLE).zip"
