# Presidio analyzer with German support (Task 4.5a).
# The stock mcr.microsoft.com/presidio-analyzer image ships English only;
# German PERSON/LOCATION NER needs a German spaCy model + an NLP conf that
# declares language "de". Global recognizers (EMAIL/IBAN/CREDIT_CARD) are
# language-agnostic and work regardless.
FROM mcr.microsoft.com/presidio-analyzer:latest

# German NLP model (md = good accuracy/size tradeoff for a dev/pilot box).
RUN python -m spacy download de_core_news_md

# NLP engine config declaring the German model. Pointed to by NLP_CONF_FILE
# (absolute path, so it does not depend on the image's default conf dir).
COPY nlp.yaml /decyra/nlp.yaml
ENV NLP_CONF_FILE=/decyra/nlp.yaml

# Recognizer-registry config: registers the global pattern recognizers
# (IBAN/Email/Phone/…) for "de". Without this the registry stays "en" and a
# language=de request runs only the NER recognizer — see recognizers.yaml.
COPY recognizers.yaml /decyra/recognizers.yaml
ENV RECOGNIZER_REGISTRY_CONF_FILE=/decyra/recognizers.yaml

# AnalyzerEngine config: supported_languages must match the NLP engine and
# the registry (all "de"), or the worker refuses to boot.
COPY analyzer.yaml /decyra/analyzer.yaml
ENV ANALYZER_CONF_FILE=/decyra/analyzer.yaml
