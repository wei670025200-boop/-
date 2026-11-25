"""Exact UTF-8 CSV byte-prefix binding. Full ledger validity is a separate gate."""
import csv
import hashlib
import re
from datetime import date
from .history_contract import HISTORY_FIELDS, _check_epoch

SCHEMA = 'DLT_TRAINING_PREFIX_SHA256_V1'

def _issue(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{5}',value) or int(value[2:])==0:
        raise ValueError('INVALID_ISSUE')
    return int(value)

def _parse_line(raw):
    if not raw.endswith(b'\n') or b'\x00' in raw:
        raise ValueError('INVALID_CSV_LINE')
    try:
        return next(csv.reader([raw.decode('utf-8')],strict=True))
    except (UnicodeError,csv.Error,StopIteration) as exc:
        raise ValueError('INVALID_CSV_LINE') from exc

def prefix_receipt(csv_bytes, trained_through_issue):
    """Hash header and all exact raw records up to and including the bound issue.
    Reject duplicate, unordered and date/issue-inconsistent records, even in suffix.
    This does not replace full schedule or financial data validation.
    """
    cutoff=_issue(trained_through_issue)
    if type(csv_bytes) is not bytes: raise TypeError('CSV_BYTES_REQUIRED')
    lines=csv_bytes.splitlines(keepends=True)
    if not lines or _parse_line(lines[0])!=HISTORY_FIELDS:
        raise ValueError('HISTORY_HEADER_MISMATCH')
    digest=hashlib.sha256(lines[0]); prev_issue=0; prev_date=None; found=False; count=0
    for line in lines[1:]:
        fields=_parse_line(line)
        if len(fields)!=len(HISTORY_FIELDS): raise ValueError('HISTORY_ROW_WIDTH')
        row=dict(zip(HISTORY_FIELDS,fields)); issue=_issue(row['issue'])
        draw_date=date.fromisoformat(row['date'])
        if issue<=prev_issue or (prev_date is not None and draw_date<=prev_date):
            raise ValueError('HISTORY_ORDER')
        if row['rule_epoch'] not in ('9TIER_2019','7TIER_2026'):
            raise ValueError('RULE_EPOCH_CONFLICT')
        _check_epoch(row['issue'],row['date'],row['rule_epoch'])
        if issue<=cutoff:
            digest.update(line); count+=1
        if issue==cutoff: found=True
        prev_issue=issue; prev_date=draw_date
    if not found: raise ValueError('TRAINED_THROUGH_ISSUE_MISSING')
    return {'schema':SCHEMA,'trained_through_issue':trained_through_issue,'prefix_rows':count,'prefix_sha256':digest.hexdigest()}

def verify_prefix(csv_bytes, expected):
    if not isinstance(expected,dict) or set(expected)!={'schema','trained_through_issue','prefix_rows','prefix_sha256'} or expected['schema']!=SCHEMA:
        raise ValueError('INVALID_PREFIX_RECEIPT')
    actual=prefix_receipt(csv_bytes,expected['trained_through_issue'])
    if actual!=expected: raise ValueError('TRAINING_PREFIX_MISMATCH')
    return actual
