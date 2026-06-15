# Downloaded Corpora Replay Evaluation

- Generated at: 2026-06-12 04:19:36
- Data root: `/home/zihai/workspace/Agent_server_design/relationshape/relationshape/eval/data/corpora`
- Order: deterministic streaming order
- Limit per corpus: `full`
- SMILE file limit: `full`

## What This Measures

- CPED has labels, so the report includes sentiment/emotion/question accuracy and macro-F1.
- Other corpora are dialogue/persona/knowledge corpora without labels that directly map to relationshape, so they are replayed for health metrics: parse count, prepare success, latency, safety trigger rate, input-type distribution, emotion distribution, and act distribution.
- No external model/API is called.

## Summary

| Corpus | n | errors | avg chars | avg ms | p95 ms | safety | questions | substantive | top input types | top emotions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| cped | 132762 | 0 | 8.4 | 0.148 | 0.222 | 0.035% | 19.0% | 83.7% | topic:119358(89.9%)；short_reply:8675(6.5%)；self_distress:2711(2.0%)；ask_advice:486(0.4%) | neutral:127731(96.2%)；happy:1266(1.0%)；annoyed:1259(0.9%)；content:575(0.4%) |
| esconv | 22842 | 0 | 70.5 | 0.145 | 0.283 | 0.000% | 10.6% | 97.3% | topic:22053(96.5%)；greeting:439(1.9%)；self_distress:177(0.8%)；short_reply:173(0.8%) | neutral:22489(98.5%)；sad:177(0.8%)；happy:176(0.8%) |
| smile | 318195 | 0 | 56.9 | 1.580 | 2.978 | 1.184% | 39.1% | 98.6% | topic:211921(66.6%)；self_distress:50334(15.8%)；ask_advice:39782(12.5%)；external_complaint:11859(3.7%) | neutral:225733(70.9%)；anxious:40530(12.7%)；sad:14158(4.4%)；annoyed:11255(3.5%) |
| cdconv | 24974 | 0 | 13.1 | 0.900 | 1.687 | 0.004% | 52.6% | 94.8% | topic:23973(96.0%)；self_distress:591(2.4%)；short_reply:112(0.4%)；character_praise:71(0.3%) | neutral:23802(95.3%)；happy:370(1.5%)；lonely:178(0.7%)；annoyed:170(0.7%) |
| kdconv | 85596 | 0 | 20.8 | 0.709 | 1.151 | 0.007% | 47.6% | 98.7% | topic:84881(99.2%)；self_distress:394(0.5%)；good_news:139(0.2%)；character_praise:68(0.1%) | neutral:82661(96.6%)；happy:2120(2.5%)；content:402(0.5%)；lonely:161(0.2%) |
| charactereval | 31347 | 0 | 27.7 | 0.115 | 0.188 | 0.144% | 46.7% | 96.1% | topic:28653(91.4%)；self_distress:1760(5.6%)；good_news:219(0.7%)；short_reply:178(0.6%) | neutral:28237(90.1%)；annoyed:842(2.7%)；happy:834(2.7%)；anxious:443(1.4%) |

## CPED Label Metrics

- labeled rows: 132762

| Target | Accuracy | Macro-F1 |
| --- | ---: | ---: |
| Sentiment | 0.330 | 0.195 |
| Emotion | 0.315 | 0.038 |
| Dialog act question | 0.870 | - |

## Details

### cped

- samples: 132762
- errors: 0
- avg/p95 chars: 8.4/17
- avg/p95 prepare_turn latency: 0.148ms/0.222ms
- safety trigger rate: 0.035% (self_harm:41(87.2%)；violence:4(8.5%)；severe_bullying:2(4.3%))
- valence: neutral:127731(96.2%)；negative:3094(2.3%)；positive:1937(1.5%)
- input types: topic:119358(89.9%)；short_reply:8675(6.5%)；self_distress:2711(2.0%)；ask_advice:486(0.4%)；good_news:460(0.3%)；farewell:314(0.2%)；character_praise:196(0.1%)；external_complaint:117(0.1%)；character_reassurance:96(0.1%)；character_rejection:78(0.1%)；ontology_question:75(0.1%)；greeting:74(0.1%)
- emotions: neutral:127731(96.2%)；happy:1266(1.0%)；annoyed:1259(0.9%)；content:575(0.4%)；anxious:475(0.4%)；lonely:428(0.3%)；sad:336(0.3%)；angry:326(0.2%)；tired:117(0.1%)；bored:106(0.1%)；warm:96(0.1%)；distress:47(0.0%)
- acts: react:128927(30.0%)；mirror:122558(28.5%)；curious:119929(27.9%)；remember:30863(7.2%)；comfort_presence:11386(2.7%)；soft_react:3350(0.8%)；validate:2903(0.7%)；care:2786(0.6%)；ask_permission_advise:2727(0.6%)；name_feeling:1064(0.2%)；advise:486(0.1%)；capitalize:460(0.1%)
- avg constraints/forbidden per turn: 3.48/6.10

### esconv

- samples: 22842
- errors: 0
- avg/p95 chars: 70.5/190
- avg/p95 prepare_turn latency: 0.145ms/0.283ms
- safety trigger rate: 0.000% (-)
- valence: neutral:22489(98.5%)；negative:177(0.8%)；positive:176(0.8%)
- input types: topic:22053(96.5%)；greeting:439(1.9%)；self_distress:177(0.8%)；short_reply:173(0.8%)
- emotions: neutral:22489(98.5%)；sad:177(0.8%)；happy:176(0.8%)
- acts: react:22665(33.1%)；curious:22492(32.9%)；mirror:22230(32.5%)；comfort_presence:350(0.5%)；soft_react:177(0.3%)；validate:177(0.3%)；care:177(0.3%)；ask_permission_advise:177(0.3%)
- avg constraints/forbidden per turn: 3.12/6.03

### smile

- samples: 318195
- errors: 0
- avg/p95 chars: 56.9/154
- avg/p95 prepare_turn latency: 1.580ms/2.978ms
- safety trigger rate: 1.184% (self_harm:3541(94.0%)；violence:104(2.8%)；severe_bullying:77(2.0%)；acute_fear:44(1.2%)；abuse:2(0.1%))
- valence: neutral:225733(70.9%)；negative:79751(25.1%)；positive:12711(4.0%)
- input types: topic:211921(66.6%)；self_distress:50334(15.8%)；ask_advice:39782(12.5%)；external_complaint:11859(3.7%)；good_news:2568(0.8%)；farewell:563(0.2%)；device_complaint:390(0.1%)；character_praise:224(0.1%)；self_blame:223(0.1%)；creative_topic:151(0.0%)；character_rejection:77(0.0%)；character_reassurance:53(0.0%)
- emotions: neutral:225733(70.9%)；anxious:40530(12.7%)；sad:14158(4.4%)；annoyed:11255(3.5%)；content:6764(2.1%)；happy:5894(1.9%)；angry:4235(1.3%)；distress:3768(1.2%)；lonely:3429(1.1%)；tired:1836(0.6%)；bored:540(0.2%)；warm:53(0.0%)
- acts: mirror:298810(23.0%)；react:223387(17.2%)；curious:210880(16.3%)；remember:149427(11.5%)；soft_react:94184(7.3%)；validate:66184(5.1%)；ask_permission_advise:61719(4.8%)；care:54325(4.2%)；comfort_presence:50340(3.9%)；advise:39782(3.1%)；name_feeling:15365(1.2%)；person_anchor:11859(0.9%)
- avg constraints/forbidden per turn: 4.36/6.94

### cdconv

- samples: 24974
- errors: 0
- avg/p95 chars: 13.1/24
- avg/p95 prepare_turn latency: 0.900ms/1.687ms
- safety trigger rate: 0.004% (self_harm:1(100.0%))
- valence: neutral:23802(95.3%)；negative:611(2.4%)；positive:561(2.2%)
- input types: topic:23973(96.0%)；self_distress:591(2.4%)；short_reply:112(0.4%)；character_praise:71(0.3%)；ask_advice:56(0.2%)；creative_topic:43(0.2%)；ontology_question:40(0.2%)；character_reassurance:30(0.1%)；good_news:28(0.1%)；external_complaint:12(0.0%)；farewell:10(0.0%)；self_blame:3(0.0%)
- emotions: neutral:23802(95.3%)；happy:370(1.5%)；lonely:178(0.7%)；annoyed:170(0.7%)；content:161(0.6%)；anxious:95(0.4%)；bored:79(0.3%)；sad:49(0.2%)；tired:33(0.1%)；warm:30(0.1%)；angry:6(0.0%)；distress:1(0.0%)
- acts: mirror:24664(25.7%)；react:24243(25.3%)；curious:24084(25.1%)；remember:19375(20.2%)；comfort_presence:703(0.7%)；soft_react:651(0.7%)；validate:607(0.6%)；care:595(0.6%)；ask_permission_advise:524(0.5%)；gratitude:101(0.1%)；name_feeling:93(0.1%)；shy_accept:71(0.1%)
- avg constraints/forbidden per turn: 4.36/6.11

### kdconv

- samples: 85596
- errors: 0
- avg/p95 chars: 20.8/43
- avg/p95 prepare_turn latency: 0.709ms/1.151ms
- safety trigger rate: 0.007% (self_harm:6(100.0%))
- valence: neutral:82661(96.6%)；positive:2522(2.9%)；negative:413(0.5%)
- input types: topic:84881(99.2%)；self_distress:394(0.5%)；good_news:139(0.2%)；character_praise:68(0.1%)；farewell:49(0.1%)；short_reply:41(0.0%)；ask_advice:9(0.0%)；external_complaint:5(0.0%)；device_complaint:3(0.0%)；character_attack:3(0.0%)；character_rejection:1(0.0%)；self_blame:1(0.0%)
- emotions: neutral:82661(96.6%)；happy:2120(2.5%)；content:402(0.5%)；lonely:161(0.2%)；annoyed:104(0.1%)；anxious:68(0.1%)；sad:46(0.1%)；bored:10(0.0%)；angry:9(0.0%)；tired:9(0.0%)；distress:6(0.0%)
- acts: mirror:85282(28.1%)；react:85135(28.1%)；curious:85016(28.0%)；remember:45285(14.9%)；comfort_presence:435(0.1%)；soft_react:411(0.1%)；validate:406(0.1%)；care:401(0.1%)；ask_permission_advise:389(0.1%)；name_feeling:155(0.1%)；capitalize:139(0.0%)；shy_accept:68(0.0%)
- avg constraints/forbidden per turn: 4.01/6.02

### charactereval

- samples: 31347
- errors: 0
- avg/p95 chars: 27.7/71
- avg/p95 prepare_turn latency: 0.115ms/0.188ms
- safety trigger rate: 0.144% (self_harm:45(100.0%))
- valence: neutral:28237(90.1%)；negative:2097(6.7%)；positive:1013(3.2%)
- input types: topic:28653(91.4%)；self_distress:1760(5.6%)；good_news:219(0.7%)；short_reply:178(0.6%)；external_complaint:167(0.5%)；ask_advice:119(0.4%)；character_praise:63(0.2%)；device_complaint:63(0.2%)；farewell:38(0.1%)；character_rejection:27(0.1%)；character_reassurance:24(0.1%)；ontology_question:13(0.0%)
- emotions: neutral:28237(90.1%)；annoyed:842(2.7%)；happy:834(2.7%)；anxious:443(1.4%)；sad:282(0.9%)；lonely:223(0.7%)；angry:183(0.6%)；content:155(0.5%)；tired:47(0.1%)；distress:45(0.1%)；bored:32(0.1%)；warm:24(0.1%)
- acts: mirror:30554(30.4%)；react:29312(29.2%)；curious:28844(28.7%)；validate:1981(2.0%)；soft_react:1960(2.0%)；comfort_presence:1938(1.9%)；ask_permission_advise:1898(1.9%)；care:1814(1.8%)；remember:513(0.5%)；name_feeling:466(0.5%)；capitalize:219(0.2%)；person_anchor:167(0.2%)
- avg constraints/forbidden per turn: 3.59/6.28

## Interpretation Notes

- High `topic` rates on KDConv/CharacterEval/CDConv are expected: these are knowledge/persona/dialogue-continuity corpora, not emotional-support corpora.
- ESConv is English; this project currently uses lightweight Chinese-oriented rules, so ESConv replay is mainly a robustness/latency smoke test, not a fair emotional accuracy benchmark.
- SMILE is closer to the target support domain; higher self-distress/ask-advice rates there are useful for checking empathy-path coverage.
- Safety trigger rate is a triage signal. High rates should be inspected manually because some corpora contain real crisis statements.
