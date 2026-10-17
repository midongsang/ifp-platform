# IFP Business Value ROI Estimation Template

> Chinese version: [roi-template.md](../roi-template.md)
> Source: IFP full documentation collection (V1.1), Appendix L.
> Purpose: Evaluate the cost and benefit of introducing IFP, quantify ROI, for project initiation and business reporting.
> Note: The estimation period is recommended to be 1 year; all fields can be filled with real business data.
> Related docs: [Business-side training material](training-business.md) · [Roadmap](roadmap.md)

---

## 1. Basic Information

| Field | Entry |
| --- | --- |
| Business domain name | ________ |
| Business type | Funds / Orders / After-sales / Approval / Other |
| Business risk level | High / Medium / Low |
| Estimation period | 12 months |
| Assessor | ________ |

---

## 2. Benefit Items (Annual Estimate)

### Benefit 1: Reduce financial loss

| Parameter | Symbol | Value |
| --- | --- | --- |
| Historical average annual rule-related financial loss amount | A | |
| Decline ratio of rule-related financial loss after introducing IFP | R1 (e.g. 40%) | |
| **Financial loss reduction benefit** | A × R1 | |

### Benefit 2: Labor savings for online incident troubleshooting and fixing

| Parameter | Symbol | Value |
| --- | --- | --- |
| Average annual number of rule-related incidents | N | |
| Average labor cost per incident (person-day) | D | |
| Labor cost per person per day | C | |
| Incident reduction ratio | R2 (e.g. 50%) | |
| **Incident labor benefit** | N × D × C × R2 | |

### Benefit 3: Labor savings for audit evidence collection

| Parameter | Symbol | Value |
| --- | --- | --- |
| Number of audits per year | M | |
| Person-days of manual evidence collation per audit | T | |
| Labor cost per person per day | C | |
| **Audit labor benefit** | M × T × C | |

### Benefit 4: Savings from requirement communication rework

| Parameter | Symbol | Value |
| --- | --- | --- |
| Average annual number of requirement reworks caused by rule ambiguity | K | |
| Person-days per rework | S | |
| Labor cost per person per day | C | |
| **Rework savings benefit** | K × S × C | |

Annual total benefit = Benefit 1 + Benefit 2 + Benefit 3 + Benefit 4

---

## 3. Cost Items (Annual Estimate)

### Cost 1: Platform R&D / procurement cost

| Parameter | Symbol | Value |
| --- | --- | --- |
| Platform R&D labor input (person-month) | DevM | |
| Labor cost per person per month | DevCost | |
| **Platform build cost** | DevM × DevCost | |

### Cost 2: Modeling labor cost (business, product, and development modeling and review)

| Parameter | Symbol | Value |
| --- | --- | --- |
| Total modeling input person-months | ModelM | |
| Labor cost per person per month | ModelCost | |
| **Modeling labor cost** | ModelM × ModelCost | |

### Cost 3: Infrastructure O&M cost

| Parameter | Symbol | Value |
| --- | --- | --- |
| Server, storage, monitoring, O&M labor | InfraCost | |

### Cost 4: Training and process build cost

| Parameter | Symbol | Value |
| --- | --- | --- |
| Training, documentation, review process build | TrainCost | |

Annual total cost = Platform build cost + Modeling labor cost + InfraCost + TrainCost

---

## 4. ROI Calculation Formula

1. **Net benefit = Annual total benefit − Annual total cost**
2. **ROI = Net benefit ÷ Annual total cost × 100%**

---

## 5. Qualitative Benefits (cannot be directly quantified; to be added when reporting)

- A unified trusted source for business rules, reducing cross-team communication conflicts;
- Improved compliance and internal-control capability, meeting regulatory audit requirements;
- Visualization of rule-change risk, reducing the risk of major changes going out of control;
- Accumulating the business domain model, making it easier for newcomers to understand business logic.

---

## 6. Sensitivity Analysis

You can adjust the following parameters to observe changes in ROI:

| Variable | Description |
| --- | --- |
| Financial loss reduction ratio R1 | Overestimation/underestimation has the largest impact on ROI |
| Modeling labor input ModelM | Impact of increased/decreased input on ROI |
| Estimated incident count N | ROI range under fluctuating scenarios |
| Audit frequency M, rework count K | Sensitivity of governance-type benefits |

---

## 7. Estimation Notes

1. IFP only targets **business-rule-related defects** and will not eliminate all incidents; code bugs and infrastructure failures are out of scope.
2. For low-risk simple CRUD business, ROI is usually negative, and implementation is not recommended.
3. Benefits in the pilot phase will be lower than in the scaled phase; phased evaluation is recommended.

---

## 8. Sample Entry: E-commerce Refund Domain (sample data)

The following is a sample filled-in form. The data is only used to demonstrate how to fill in the form; for actual estimation, please replace it with real business data.

**Basic information**

| Field | Entry |
| --- | --- |
| Business domain name | E-commerce refund domain (`RefundContext`) |
| Business type | Funds / After-sales |
| Business risk level | High |
| Estimation period | 12 months |

**Benefit items**

| Parameter | Symbol | Sample value | Calculation | Benefit (sample, unit: RMB 10k) |
| --- | --- | --- | --- | --- |
| Historical average annual rule-related financial loss | A | 200 | — | — |
| Financial loss reduction ratio | R1 | 40% | 200 × 40% | **80** |
| Average annual number of rule-related incidents | N | 10 times | — | — |
| Labor per incident (person-day) | D | 5 person-days | — | — |
| Labor cost per person per day | C | 0.15 | — | — |
| Incident reduction ratio | R2 | 50% | 10 × 5 × 0.15 × 50% | **3.75** |
| Number of audits per year | M | 2 times | — | — |
| Person-days of collation per audit | T | 10 person-days | 2 × 10 × 0.15 | **3** |
| Average annual number of reworks | K | 8 times | — | — |
| Person-days per rework | S | 3 person-days | 8 × 3 × 0.15 | **3.6** |
| **Annual total benefit** | | | 80 + 3.75 + 3 + 3.6 | **≈ 90.35** |

**Cost items**

| Parameter | Symbol | Sample value | Calculation | Cost (sample, unit: RMB 10k) |
| --- | --- | --- | --- | --- |
| Platform R&D labor | DevM | 12 person-months | — | — |
| Labor cost per person per month | DevCost | 2.5 | 12 × 2.5 | **30** |
| Modeling input person-months | ModelM | 6 person-months | — | — |
| Labor cost per person per month | ModelCost | 2.0 | 6 × 2.0 | **12** |
| Infrastructure O&M | InfraCost | — | — | **3** |
| Training and process build | TrainCost | — | — | **2** |
| **Annual total cost** | | | 30 + 12 + 3 + 2 | **47** |

**ROI result**

| Metric | Calculation | Sample result |
| --- | --- | --- |
| Net benefit | 90.35 − 47 | **43.35** |
| ROI | 43.35 ÷ 47 × 100% | **≈ 92%** |

Conclusion (sample): This refund domain nearly breaks even after a one-year pilot and produces a positive return, with the largest share of benefit coming from financial loss reduction.
As modeling standards are consolidated and the model is reused across domains, ROI in the scaled phase usually climbs further.
