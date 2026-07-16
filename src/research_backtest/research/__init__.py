"""Project 1 — 기업·산업 리서치 및 투자 가설 생성 (README §2 Project 1).

README §25의 disclosures/·research/ 영역에 해당한다. 공시 원문 분석,
Evidence 생성, LLM 기업분석·보고서·투자 가설을 담당하며 Phase C(C1~C2)에서
구현한다. 데이터 수집·정규화는 core에 있고, 이 패키지는 그 산출물을 소비한다.

하위 패키지:

- :mod:`research.evidence` — A4 재무 parquet에서 결정적 Python 계산으로 재무
  Evidence를 생성하는 Evidence Store(W3a-E1). Point-in-Time을 강제하며 LLM은
  이 Evidence를 소비할 뿐 재계산하지 않는다(README §18.1).
"""
