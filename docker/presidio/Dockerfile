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
