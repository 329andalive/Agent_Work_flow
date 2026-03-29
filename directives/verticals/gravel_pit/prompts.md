# Gravel Pit & Materials — Agent Prompt Language

## Invoice agent prompt additions
- Always show quantity in yards and price per yard: "10 yards 2-inch minus @ $14/yard"
- FOB and Delivered are different line items — never combine them
- Tax applies to product AND delivery together — never tax labor separately
- Self-load jobs are draft invoices only — never send until office approves
- Keep it simple: product, quantity, price per yard, total. Contractors know what they ordered.

## Self-load workflow
- When a contractor texts "got 10 yards 3/4 minus" this is a self-load
- Create a DRAFT invoice — do not send to customer
- Notify office immediately: who loaded, what product, how many yards, timestamp
- Office reconciles against physical notebook before approving and sending
- This replaces the forgotten Friday notebook problem — system has the record

## Proposal agent prompt additions
- Always clarify FOB vs delivered before quoting — price difference is significant
- For delivered quotes always ask approximate distance or address
- Default assumption: delivered, local rate ($10/yard add-on)
- Never quote $0 for any line item

## Scheduling notes
- Delivery jobs: book 1-3 days out, depends on truck availability
- Self-load: no scheduling needed, contractor loads themselves
- Spread/grade: usually same day as delivery or next day
- Site work (house lot, driveway, septic install): book 1-2 weeks out
