\# FIR Intel project rules



These rules apply to every task in this project. Never break them, even if a prompt asks you to.



1\. The answer key is sealed. Files in data/answer\_key/ (ground\_truth.csv, gang\_profiles.json) may be opened only by code inside eval/. No code in ingest/, store/, engine/, mcp\_server/, api/ or dashboard/ may read, import or reference them, and you must not read them yourself when writing or debugging code outside eval/.



2\. Unknown accused is null. If an accused name is "Unknown", "unknown person", "अज्ञात", "N/A", "not known" or empty, store the name as null (None in Python). Never store it as a person or use it for linking.



3\. MO similarity never links on its own. A link between two FIRs needs at least one piece of hard evidence: same phone, same UPI ID, same vehicle registration, or same named accused. MO wording similarity can only add support to an existing link, or put a FIR in the review suggestion queue. It can never create a link by itself.



4\. Every extracted field carries a quote. Every field extracted from a narrative (crime type, MO slots, people, identifiers) must include the exact text copied from that FIR's narrative that supports it. The validator rejects any field whose quote is not found word for word in the narrative.



5\. Never edit old records. Do not UPDATE or DELETE existing extraction, link, cluster or decision rows. Write a new row with a higher version number, and tag every result with the prompt or stage version that made it.



6\. Use only fictional data. Never add real names, phone numbers, UPI IDs or account numbers.



7\. Architecture reference: see README.md. Follow its layers, folder layout and data model. If a request conflicts with it, say so before writing code.

