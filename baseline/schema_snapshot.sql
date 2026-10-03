CREATE TABLE accounts (
	id INTEGER NOT NULL, 
	code VARCHAR(20) NOT NULL, 
	name_ar VARCHAR(200) NOT NULL, 
	account_type VARCHAR(9) NOT NULL, 
	parent_id INTEGER, 
	currency_code VARCHAR(3) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	is_group BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(parent_id) REFERENCES accounts (id)
);

CREATE TABLE inventory_movements (
	id INTEGER NOT NULL, 
	item_id INTEGER NOT NULL, 
	warehouse_id INTEGER NOT NULL, 
	direction VARCHAR(3) NOT NULL, 
	quantity NUMERIC(14, 3) NOT NULL, 
	unit_cost NUMERIC(14, 4) NOT NULL, 
	movement_date DATETIME NOT NULL, 
	source_type VARCHAR(30) NOT NULL, 
	source_id INTEGER, 
	note TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(item_id) REFERENCES items (id), 
	FOREIGN KEY(warehouse_id) REFERENCES warehouses (id)
);

CREATE TABLE invoice_lines (
	id INTEGER NOT NULL, 
	invoice_id INTEGER NOT NULL, 
	item_id INTEGER NOT NULL, 
	quantity NUMERIC(14, 3) NOT NULL, 
	unit_price NUMERIC(14, 4) NOT NULL, 
	discount_percent NUMERIC(5, 2) NOT NULL, 
	discount_amount NUMERIC(14, 2) NOT NULL, 
	tax_rate NUMERIC(5, 2) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(invoice_id) REFERENCES invoices (id), 
	FOREIGN KEY(item_id) REFERENCES items (id)
);

CREATE TABLE invoices (
	id INTEGER NOT NULL, 
	invoice_no VARCHAR(30) NOT NULL, 
	kind VARCHAR(15) NOT NULL, 
	invoice_date DATE NOT NULL, 
	party_name VARCHAR(200) NOT NULL, 
	currency_code VARCHAR(3) NOT NULL, 
	exchange_rate NUMERIC(14, 6) NOT NULL, 
	status VARCHAR(9) NOT NULL, 
	discount_percent NUMERIC(5, 2) NOT NULL, 
	discount_amount NUMERIC(14, 2) NOT NULL, 
	original_invoice_id INTEGER, 
	journal_entry_id INTEGER, 
	warehouse_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (invoice_no), 
	FOREIGN KEY(original_invoice_id) REFERENCES invoices (id), 
	FOREIGN KEY(journal_entry_id) REFERENCES journal_entries (id), 
	FOREIGN KEY(warehouse_id) REFERENCES warehouses (id)
);

CREATE TABLE items (
	id INTEGER NOT NULL, 
	sku VARCHAR(50) NOT NULL, 
	name_ar VARCHAR(200) NOT NULL, 
	unit VARCHAR(20) NOT NULL, 
	category VARCHAR(100), 
	cost_method VARCHAR(7) NOT NULL, 
	reorder_point NUMERIC(14, 3) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	inventory_account_id INTEGER, 
	sales_account_id INTEGER, 
	cogs_account_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(inventory_account_id) REFERENCES accounts (id), 
	FOREIGN KEY(sales_account_id) REFERENCES accounts (id), 
	FOREIGN KEY(cogs_account_id) REFERENCES accounts (id)
);

CREATE UNIQUE INDEX ix_accounts_code ON accounts (code);

CREATE UNIQUE INDEX ix_items_sku ON items (sku);

CREATE TABLE journal_entries (
	id INTEGER NOT NULL, 
	entry_date DATE NOT NULL, 
	ref_no VARCHAR(30) NOT NULL, 
	description TEXT, 
	currency_code VARCHAR(3) NOT NULL, 
	exchange_rate NUMERIC(14, 6) NOT NULL, 
	source_type VARCHAR(30) NOT NULL, 
	source_id INTEGER, 
	is_reversal_of INTEGER, 
	created_at DATETIME NOT NULL, 
	status VARCHAR(9) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (ref_no), 
	FOREIGN KEY(is_reversal_of) REFERENCES journal_entries (id)
);

CREATE TABLE journal_lines (
	id INTEGER NOT NULL, 
	entry_id INTEGER NOT NULL, 
	account_id INTEGER NOT NULL, 
	debit NUMERIC(14, 2) NOT NULL, 
	credit NUMERIC(14, 2) NOT NULL, 
	debit_base NUMERIC(14, 2) NOT NULL, 
	credit_base NUMERIC(14, 2) NOT NULL, 
	line_currency_code VARCHAR(3), 
	line_exchange_rate NUMERIC(14, 6), 
	cost_center VARCHAR(50), 
	PRIMARY KEY (id), 
	CONSTRAINT ck_debit_xor_credit CHECK ((debit = 0 AND credit >= 0) OR (credit = 0 AND debit >= 0)), 
	FOREIGN KEY(entry_id) REFERENCES journal_entries (id), 
	FOREIGN KEY(account_id) REFERENCES accounts (id)
);

CREATE TABLE settings (
	"key" VARCHAR(100) NOT NULL, 
	value TEXT NOT NULL, 
	PRIMARY KEY ("key")
);

CREATE TABLE stock_transfers (
	id INTEGER NOT NULL, 
	transfer_no VARCHAR(30) NOT NULL, 
	transfer_date DATE NOT NULL, 
	item_id INTEGER NOT NULL, 
	from_warehouse_id INTEGER NOT NULL, 
	to_warehouse_id INTEGER NOT NULL, 
	quantity NUMERIC(14, 3) NOT NULL, 
	note TEXT, 
	PRIMARY KEY (id), 
	UNIQUE (transfer_no), 
	FOREIGN KEY(item_id) REFERENCES items (id), 
	FOREIGN KEY(from_warehouse_id) REFERENCES warehouses (id), 
	FOREIGN KEY(to_warehouse_id) REFERENCES warehouses (id)
);

CREATE TABLE warehouses (
	id INTEGER NOT NULL, 
	name_ar VARCHAR(100) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

