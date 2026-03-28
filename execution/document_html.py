"""
document_html.py — Builds mobile-first edit/view HTML pages for proposals and invoices

Single function handles both document types. Returns a complete HTML string
with inline CSS and JS. Zero external dependencies — works offline.

Usage:
    from execution.document_html import build_document_html
    html = build_document_html(document, client, customer, doc_type='proposal', edit_mode=False)
"""

import json
from datetime import datetime


def build_document_html(
    document: dict,
    client: dict,
    customer: dict,
    doc_type: str = "proposal",
    edit_mode: bool = False,
) -> str:
    """
    Build a complete HTML page for a proposal or invoice.

    Args:
        document:   Full row from proposals or invoices table
        client:     Full row from clients table
        customer:   Full row from customers table
        doc_type:   'proposal' or 'invoice'
        edit_mode:  True = contenteditable fields + save bar; False = view only

    Returns:
        Complete HTML string ready to serve or upload.
    """
    # --- Extract data ---
    business_name = client.get("business_name", "Business") if client else "Business"
    owner_name = client.get("owner_name", "") if client else ""
    tagline = client.get("service_area", "") if client else ""

    customer_name = customer.get("customer_name", "Customer") if customer else "Customer"
    customer_address = (customer.get("customer_address") or customer.get("address") or "") if customer else ""

    doc_id = document.get("id", "")
    doc_number = doc_id[-8:].upper() if doc_id else "00000000"
    edit_token = document.get("edit_token", "")
    date_str = datetime.now().strftime("%B %d, %Y")

    # Line items — stored as JSONB, could be list or None
    line_items = document.get("line_items") or []
    if isinstance(line_items, str):
        try:
            line_items = json.loads(line_items)
        except (json.JSONDecodeError, TypeError):
            line_items = []

    # Notes — proposal_text or invoice_text
    if doc_type == "proposal":
        notes = document.get("proposal_text", "") or ""
        amount_field = "amount_estimate"
    else:
        notes = document.get("invoice_text", "") or ""
        amount_field = "amount_due"

    total_amount = float(document.get(amount_field) or 0)
    tax_rate = float(document.get("tax_rate") or 0)
    subtotal = float(document.get("subtotal") or total_amount)
    tax_amount = float(document.get("tax_amount") or 0)

    # Compute grand total from line items if they exist
    if line_items:
        subtotal = sum(float(item.get("total", 0)) for item in line_items)
        tax_amount = round(subtotal * tax_rate, 2)
        total_amount = round(subtotal + tax_amount, 2)

    # Label strings
    doc_label = "Estimate" if doc_type == "proposal" else "Invoice"
    doc_prefix = "EST" if doc_type == "proposal" else "INV"

    # Build line items HTML rows
    line_items_html = ""
    for i, item in enumerate(line_items):
        desc = item.get("description", "")
        qty = item.get("qty", 1)
        unit_price = item.get("unit_price", 0)
        line_total = item.get("total", 0)

        if edit_mode:
            taxable = item.get("taxable", False)
            tax_cls = "tax-on" if taxable else ""
            tax_label = "TAX ✓" if taxable else "TAX"
            line_items_html += f"""
            <tr data-row="{i}" data-taxable="{'true' if taxable else 'false'}">
              <td contenteditable="true" class="editable" data-field="description">{_esc(desc)}</td>
              <td contenteditable="true" class="editable num" data-field="qty">{qty}</td>
              <td contenteditable="true" class="editable num" data-field="unit_price">{unit_price:.2f}</td>
              <td class="num line-total">{line_total:.2f}</td>
              <td class="tax-cell"><button class="tax-btn {tax_cls}" onclick="toggleTax(this)">{tax_label}</button></td>
              <td class="remove-cell"><button class="remove-btn" onclick="removeRow(this)" aria-label="Remove line">&times;</button></td>
            </tr>"""
        else:
            line_items_html += f"""
            <tr>
              <td>{_esc(desc)}</td>
              <td class="num">{qty}</td>
              <td class="num">${unit_price:.2f}</td>
              <td class="num">${line_total:.2f}</td>
            </tr>"""

    # If no line items, add one empty row in edit mode
    if not line_items and edit_mode:
        line_items_html = f"""
        <tr data-row="0" data-taxable="false">
          <td contenteditable="true" class="editable" data-field="description">Service description</td>
          <td contenteditable="true" class="editable num" data-field="qty">1</td>
          <td contenteditable="true" class="editable num" data-field="unit_price">0.00</td>
          <td class="num line-total">0.00</td>
          <td class="tax-cell"><button class="tax-btn" onclick="toggleTax(this)">TAX</button></td>
          <td class="remove-cell"><button class="remove-btn" onclick="removeRow(this)" aria-label="Remove line">&times;</button></td>
        </tr>"""

    # Tax column header for edit mode
    tax_col_header = '<th class="tax-cell" style="width:50px">Tax</th>' if edit_mode else ""

    # Remove column header for edit mode
    remove_col_header = '<th class="remove-cell"></th>' if edit_mode else ""
    remove_col_css = "table .remove-cell { width: 40px; text-align: center; }" if edit_mode else ""

    # Notes section
    if edit_mode:
        notes_html = f'<textarea id="notes" class="notes-edit" placeholder="Add notes for the customer...">{_esc(notes)}</textarea>'
    else:
        notes_paragraphs = "".join(f"<p>{_esc(line)}</p>" for line in notes.split("\n") if line.strip())
        notes_html = f'<div class="notes-view">{notes_paragraphs}</div>' if notes_paragraphs else ""

    # Bottom bar
    if edit_mode:
        bottom_bar = f"""
        <div class="sticky-bar">
          <button id="save-btn" class="btn btn-primary" onclick="saveDocument()">Save Changes</button>
        </div>"""
    else:
        edit_url = f"/doc/edit/{edit_token}?type={doc_type}"
        bottom_bar = f"""
        <div class="action-bar">
          <button class="btn btn-send" onclick="sendDocument()">Send to Customer</button>
          <a href="{edit_url}" class="btn btn-outline">Edit</a>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{doc_label} — {business_name}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
      background: #f4f5f7;
      color: #1a1a1a;
      padding: 0;
      min-height: 100vh;
      -webkit-text-size-adjust: 100%;
    }}
    .card {{
      max-width: 680px;
      margin: 0 auto;
      background: #fff;
      min-height: 100vh;
    }}
    @media (min-width: 720px) {{
      body {{ padding: 2rem 1rem; }}
      .card {{
        min-height: auto;
        border-radius: 12px;
        box-shadow: 0 2px 20px rgba(0,0,0,0.08);
        margin-top: 1rem;
        margin-bottom: 6rem;
      }}
    }}

    /* Header */
    .header {{
      background: #1a2744;
      color: #fff;
      padding: 1.5rem;
    }}
    .header h1 {{
      font-size: 1.4rem;
      font-weight: 700;
      letter-spacing: -0.01em;
    }}
    .header .tagline {{
      font-size: 0.8rem;
      opacity: 0.6;
      margin-top: 0.25rem;
    }}
    .header hr {{
      border: none;
      border-top: 1px solid rgba(255,255,255,0.15);
      margin: 1rem 0;
    }}
    .header .doc-meta {{
      display: flex;
      justify-content: space-between;
      font-size: 0.85rem;
      opacity: 0.8;
    }}

    /* Body */
    .body {{ padding: 1.5rem; }}

    /* Customer block */
    .customer-block {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1rem;
      margin-bottom: 1.5rem;
      padding-bottom: 1.25rem;
      border-bottom: 1px solid #e5e7eb;
    }}
    .customer-block .label {{
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #64748b;
      margin-bottom: 0.2rem;
    }}
    .customer-block .value {{
      font-size: 0.9rem;
      font-weight: 500;
    }}

    /* Line items table */
    .items-section {{ margin-bottom: 1.5rem; }}
    .items-section h3 {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #64748b;
      margin-bottom: 0.75rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.88rem;
    }}
    thead th {{
      text-align: left;
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #64748b;
      padding: 0.5rem 0.4rem;
      border-bottom: 2px solid #e5e7eb;
    }}
    thead th.num, td.num {{ text-align: right; }}
    tbody td {{
      padding: 0.6rem 0.4rem;
      border-bottom: 1px solid #f0f0f0;
      vertical-align: top;
    }}
    {remove_col_css}

    /* Edit mode styles */
    .editable {{
      outline: none;
      border-radius: 4px;
      padding: 2px 4px;
      margin: -2px -4px;
      transition: box-shadow 0.15s;
    }}
    .editable:focus {{
      box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.4);
      background: #f8faff;
    }}
    .remove-btn {{
      background: none;
      border: none;
      color: #94a3b8;
      font-size: 1.2rem;
      cursor: pointer;
      padding: 0 4px;
      line-height: 1;
    }}
    .remove-btn:hover {{ color: #ef4444; }}
    .add-row {{
      display: inline-block;
      margin-top: 0.5rem;
      font-size: 0.85rem;
      color: #3b82f6;
      cursor: pointer;
      border: none;
      background: none;
      padding: 0.3rem 0;
      font-weight: 500;
    }}
    .add-row:hover {{ text-decoration: underline; }}

    /* Totals */
    .totals {{
      margin: 1.25rem 0;
      padding: 1rem;
      background: #f8faf8;
      border-radius: 8px;
    }}
    .total-line {{
      display: flex;
      justify-content: space-between;
      padding: 0.35rem 0;
      font-size: 0.9rem;
      color: #444;
    }}
    .total-line.grand {{
      padding-top: 0.6rem;
      margin-top: 0.4rem;
      border-top: 2px solid #2d6a4f;
      font-size: 1.15rem;
      font-weight: 700;
      color: #2d6a4f;
    }}
    .tax-cell {{ width: 50px; text-align: center; }}
    .tax-btn {{
      font-size: 0.7rem;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 4px;
      border: 1px solid #d1d5db;
      background: #fff;
      color: #888;
      cursor: pointer;
      letter-spacing: 0.03em;
    }}
    .tax-btn:hover {{ border-color: #f59e0b; color: #f59e0b; }}
    .tax-btn.tax-on {{
      background: #FAEEDA;
      color: #854F0B;
      border-color: #d4a844;
    }}

    /* Notes */
    .notes-section {{ margin: 1.25rem 0; }}
    .notes-section h3 {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #64748b;
      margin-bottom: 0.5rem;
    }}
    .notes-edit {{
      width: 100%;
      min-height: 80px;
      font-family: inherit;
      font-size: 0.9rem;
      padding: 0.75rem;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      resize: vertical;
      line-height: 1.6;
    }}
    .notes-edit:focus {{
      outline: none;
      box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.4);
      border-color: #3b82f6;
    }}
    .notes-view p {{
      font-size: 0.9rem;
      line-height: 1.6;
      margin-bottom: 0.5rem;
      color: #444;
    }}

    /* Bottom bars */
    .sticky-bar {{
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      background: #fff;
      padding: 0.75rem 1rem;
      box-shadow: 0 -2px 12px rgba(0,0,0,0.08);
      display: flex;
      justify-content: center;
      z-index: 100;
    }}
    .action-bar {{
      display: flex;
      gap: 0.75rem;
      margin-top: 1.5rem;
      padding-bottom: 1rem;
    }}
    .btn {{
      display: inline-block;
      text-align: center;
      padding: 0.85rem 1.5rem;
      border-radius: 8px;
      font-size: 1rem;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      border: none;
      transition: opacity 0.15s;
      min-width: 140px;
    }}
    .btn:active {{ opacity: 0.85; }}
    .btn-primary {{
      background: #1a2744;
      color: #fff;
      width: 100%;
      max-width: 400px;
    }}
    .btn-send {{
      flex: 2;
      background: #2d6a4f;
      color: #fff;
    }}
    .btn-outline {{
      flex: 1;
      background: #fff;
      color: #1a2744;
      border: 2px solid #1a2744;
    }}

    /* Footer */
    .footer {{
      padding: 1rem 1.5rem;
      font-size: 0.78rem;
      color: #94a3b8;
      text-align: center;
      border-top: 1px solid #e5e7eb;
      {"margin-bottom: 4.5rem;" if edit_mode else ""}
    }}
    .footer a {{ color: #94a3b8; text-decoration: none; }}

    /* Toast */
    .toast {{
      display: none;
      position: fixed;
      top: 1rem;
      left: 50%;
      transform: translateX(-50%);
      background: #2d6a4f;
      color: #fff;
      padding: 0.6rem 1.2rem;
      border-radius: 8px;
      font-size: 0.9rem;
      font-weight: 500;
      z-index: 200;
      box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }}
    .toast.error {{ background: #dc2626; }}
  </style>
</head>
<body>
  <div id="toast" class="toast"></div>
  <div class="card">
    <div class="header">
      <h1>{_esc(business_name)}</h1>
      {"<div class='tagline'>" + _esc(tagline) + "</div>" if tagline else ""}
      <hr>
      <div class="doc-meta">
        <span>{doc_label} #{doc_prefix}-{doc_number}</span>
        <span>{date_str}</span>
      </div>
    </div>
    <div class="body">
      <div class="customer-block">
        <div>
          <div class="label">Customer</div>
          <div class="value">{_esc(customer_name)}</div>
        </div>
        <div>
          <div class="label">{"Address" if customer_address else "Document"}</div>
          <div class="value">{_esc(customer_address) if customer_address else f"{doc_prefix}-{doc_number}"}</div>
        </div>
      </div>

      <div class="items-section">
        <h3>Line Items</h3>
        <table id="items-table">
          <thead>
            <tr>
              <th>Description</th>
              <th class="num">Qty</th>
              <th class="num">Unit Price</th>
              <th class="num">Total</th>
              {tax_col_header}
              {remove_col_header}
            </tr>
          </thead>
          <tbody id="items-body">
            {line_items_html}
          </tbody>
        </table>
        {"<button class='add-row' onclick='addRow()'>+ Add Line</button>" if edit_mode else ""}
      </div>

      <div class="totals">
        <div class="total-line">
          <span>Subtotal</span>
          <span id="subtotal">${subtotal:.2f}</span>
        </div>
        <div class="total-line">
          <span>Tax (5.5% Maine — on marked items)</span>
          <span id="tax-amount">${tax_amount:.2f}</span>
        </div>
        <div class="total-line grand">
          <span>Total</span>
          <span id="grand-total">${total_amount:.2f}</span>
        </div>
      </div>

      <div class="notes-section">
        <h3>Notes</h3>
        {notes_html}
      </div>

      {bottom_bar}
    </div>
    <div class="footer">
      Questions? Reply to this message. Powered by <a href="https://bolts11.com">Bolts11</a>.
    </div>
  </div>

  <script>
    var editToken = '{edit_token}';
    var docType = '{doc_type}';
    var isEditMode = {'true' if edit_mode else 'false'};

    // --- Toast ---
    function showToast(msg, isError) {{
      var t = document.getElementById('toast');
      t.textContent = msg;
      t.className = 'toast' + (isError ? ' error' : '');
      t.style.display = 'block';
      setTimeout(function() {{ t.style.display = 'none'; }}, 2500);
    }}

    // --- Recalculate totals ---
    function toggleTax(btn) {{
      var row = btn.closest('tr');
      var isTaxed = row.getAttribute('data-taxable') === 'true';
      row.setAttribute('data-taxable', isTaxed ? 'false' : 'true');
      btn.classList.toggle('tax-on', !isTaxed);
      btn.textContent = isTaxed ? 'TAX' : 'TAX ✓';
      recalculate();
    }}

    function recalculate() {{
      var rows = document.querySelectorAll('#items-body tr');
      var subtotal = 0;
      var taxableTotal = 0;
      rows.forEach(function(row) {{
        var qtyEl = row.querySelector('[data-field="qty"]');
        var priceEl = row.querySelector('[data-field="unit_price"]');
        var totalEl = row.querySelector('.line-total');
        if (!qtyEl || !priceEl || !totalEl) return;
        var qty = parseFloat(qtyEl.textContent) || 0;
        var price = parseFloat(priceEl.textContent) || 0;
        var lineTotal = Math.round(qty * price * 100) / 100;
        totalEl.textContent = lineTotal.toFixed(2);
        subtotal += lineTotal;
        if (row.getAttribute('data-taxable') === 'true') {{
          taxableTotal += lineTotal;
        }}
      }});

      var taxRate = 0.055; // Maine 5.5%
      var taxAmount = Math.round(taxableTotal * taxRate * 100) / 100;
      var grandTotal = Math.round((subtotal + taxAmount) * 100) / 100;

      document.getElementById('subtotal').textContent = '$' + subtotal.toFixed(2);
      document.getElementById('tax-amount').textContent = '$' + taxAmount.toFixed(2);
      document.getElementById('grand-total').textContent = '$' + grandTotal.toFixed(2);
    }}

    // --- Collect line items ---
    function collectLineItems() {{
      var rows = document.querySelectorAll('#items-body tr');
      var items = [];
      rows.forEach(function(row) {{
        var desc = (row.querySelector('[data-field="description"]') || {{}}).textContent || '';
        var qty = parseFloat((row.querySelector('[data-field="qty"]') || {{}}).textContent) || 0;
        var unitPrice = parseFloat((row.querySelector('[data-field="unit_price"]') || {{}}).textContent) || 0;
        var total = Math.round(qty * unitPrice * 100) / 100;
        var taxable = row.getAttribute('data-taxable') === 'true';
        items.push({{ description: desc.trim(), qty: qty, unit_price: unitPrice, total: total, taxable: taxable }});
      }});
      return items;
    }}

    // --- Add row ---
    function addRow() {{
      var tbody = document.getElementById('items-body');
      var idx = tbody.rows.length;
      var tr = document.createElement('tr');
      tr.setAttribute('data-row', idx);
      tr.setAttribute('data-taxable', 'false');
      tr.innerHTML = '<td contenteditable="true" class="editable" data-field="description">New item</td>' +
        '<td contenteditable="true" class="editable num" data-field="qty">1</td>' +
        '<td contenteditable="true" class="editable num" data-field="unit_price">0.00</td>' +
        '<td class="num line-total">0.00</td>' +
        '<td class="tax-cell"><button class="tax-btn" onclick="toggleTax(this)">TAX</button></td>' +
        '<td class="remove-cell"><button class="remove-btn" onclick="removeRow(this)">&times;</button></td>';
      tbody.appendChild(tr);
      attachListeners(tr);
      recalculate();
    }}

    // --- Remove row ---
    function removeRow(btn) {{
      var row = btn.closest('tr');
      if (row) row.remove();
      recalculate();
    }}

    // --- Attach keyup listeners ---
    function attachListeners(el) {{
      el.querySelectorAll('.editable').forEach(function(cell) {{
        cell.addEventListener('keyup', recalculate);
        cell.addEventListener('blur', recalculate);
      }});
    }}

    // --- Save ---
    function saveDocument() {{
      var btn = document.getElementById('save-btn');
      btn.textContent = 'Saving...';
      btn.disabled = true;

      var notesEl = document.getElementById('notes');
      var items = collectLineItems();
      // Calculate tax_rate: if any item is taxable, rate is 0.055 (Maine 5.5%)
      var hasTaxable = items.some(function(it) {{ return it.taxable; }});
      var taxRate = hasTaxable ? 0.055 : 0;

      var payload = {{
        edit_token: editToken,
        doc_type: docType,
        line_items: items,
        tax_rate: taxRate,
        notes: notesEl ? notesEl.value : ''
      }};

      fetch('/doc/save', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(payload)
      }})
      .then(function(r) {{ return r.json(); }})
      .then(function(data) {{
        if (data.success) {{
          btn.textContent = '\\u2713 Saved';
          btn.style.background = '#2d6a4f';
          showToast('\\u2713 Saved successfully');
          setTimeout(function() {{
            btn.textContent = 'Save Changes';
            btn.style.background = '#1a2744';
            btn.disabled = false;
          }}, 1500);
        }} else {{
          btn.textContent = 'Save Changes';
          btn.disabled = false;
          showToast('Save failed: ' + (data.error || 'Unknown error'), true);
        }}
      }})
      .catch(function(err) {{
        btn.textContent = 'Save Changes';
        btn.disabled = false;
        showToast('Network error', true);
      }});
    }}

    // --- Send to customer ---
    function sendDocument() {{
      if (!confirm('Send this {doc_label.lower()} to the customer?')) return;

      var btn = document.querySelector('.btn-send');
      btn.textContent = 'Sending...';
      btn.disabled = true;

      fetch('/doc/send', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ edit_token: editToken, doc_type: docType }})
      }})
      .then(function(r) {{ return r.json(); }})
      .then(function(data) {{
        if (data.success) {{
          btn.textContent = '\\u2713 Sent!';
          btn.style.background = '#16a34a';
          showToast('\\u2713 Sent to customer');
        }} else {{
          btn.textContent = 'Send to Customer';
          btn.disabled = false;
          showToast('Send failed: ' + (data.error || 'Unknown error'), true);
        }}
      }})
      .catch(function(err) {{
        btn.textContent = 'Send to Customer';
        btn.disabled = false;
        showToast('Network error', true);
      }});
    }}

    // --- Init: attach listeners to existing rows ---
    if (isEditMode) {{
      document.querySelectorAll('#items-body tr').forEach(attachListeners);
      recalculate();
    }}
  </script>
</body>
</html>"""


def _esc(text: str) -> str:
    """Escape HTML special characters."""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
