\# AI Assistance and Prompt History



\## Purpose



AI assistance was used during development of the Exam Scheduling and Room Allocation System to help design, implement, debug, and verify the project.



\*\*Transparency note:\*\* This document is a reconstructed summary of the prompt themes from the development conversation, not a verbatim transcript of every prompt. It should be updated with exact prompts from any other AI tools used before submission.



\## 1. Project Requirements and Architecture



\- Help plan an exam scheduling and room allocation system based on student enrollment, room capacity, availability, accommodations, and invigilator constraints.

\- Review the database entities, relationships, primary keys, foreign keys, lifecycle states, and effective dates needed for the project.

\- Identify project deliverables and gaps against the recruitment assignment requirements.



\## 2. Synthetic Data Generation



\- Help implement deterministic synthetic data generation with a fixed random seed and configurable dataset size.

\- Generate master records before dependent records and ensure foreign keys reference valid records.

\- Include realistic edge cases for student exam conflicts, room capacity, invigilator double-booking, accommodation requirements, and room availability.

\- Check database integrity and reproducibility across generated datasets.



\## 3. Scheduling and Validation



\- Help implement exam scheduling with room, time-slot, student-conflict, capacity, accommodation, and invigilator constraints.

\- Review how unscheduled exams receive reason codes.

\- Validate schedule assignments against the implemented business rules.

\- Preserve scheduling changes in an audit history.



\## 4. Testing and Debugging



\- Help investigate errors in Python scripts, database access, exports, and validation.

\- Check whether generated datasets are reproducible using the same seed.

\- Test scheduling edge cases and verify export counts and checksums.

\- Review whether audit events are preserved and protected from modification.



\## 5. Dashboard and Exports



\- Help develop a Streamlit dashboard to display schedule summaries, assignments, unscheduled exams, audit history, and data overview.

\- Help export database tables to CSV and JSON, create an SQL dump, and generate a manifest for verification.

\- Help document project setup and execution commands in the README.



\## 6. Human Verification



AI-generated suggestions and code must be reviewed against the actual implementation. Reported test results should correspond to commands actually executed. The project owner is responsible for understanding the code, validating the results, and correcting any remaining issues.



