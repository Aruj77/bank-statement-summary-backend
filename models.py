from decimal import Decimal
from typing import List, Optional, Any
from pydantic import BaseModel, Field


class ParserDetection(BaseModel):
    parser: str = Field(description="Selected parser ID, e.g., PARSER_A, PARSER_B")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0")
    table_type: str = Field(description="Descriptive identifier of the table layout")
    date_column: Optional[str] = None
    value_date_column: Optional[str] = None
    description_column: Optional[str] = None
    amount_column: Optional[str] = None
    debit_column: Optional[str] = None
    credit_column: Optional[str] = None
    balance_column: Optional[str] = None
    reference_column: Optional[str] = None
    debit_credit_method: str = Field(
        description="EXPLICIT_COLUMNS | CR_DR_FLAG | BALANCE_DIFFERENCE | VALUE_SIGN"
    )
    multiline_transactions: bool = False
    layout_required: bool = False
    reasoning: Optional[str] = None


class NormalizedTransaction(BaseModel):
    index: int = Field(alias="_index")
    sNo: Optional[int] = None
    date: str
    valueDate: Optional[str] = None
    remarks: str
    description: str
    txnAmount: Decimal
    withdrawal: Decimal
    deposit: Decimal
    amount: Decimal
    balance: Optional[Decimal] = None
    type: str  # "DEBIT" | "CREDIT"


class StatementSummary(BaseModel):
    transactionCount: int
    totalCredit: str
    totalDebit: str
    openingBalance: Optional[str] = None
    closingBalance: Optional[str] = None
    reconciliationVerified: bool
    auditDifference: Optional[str] = "0.00"


class ExtractionResponse(BaseModel):
    status: str
    bank: str
    parser: str
    parserConfidence: float
    summary: StatementSummary
    transactions: List[NormalizedTransaction]

class StatementPeriod(BaseModel):
    startDate: Optional[str] = None
    endDate: Optional[str] = None


class StatementMetadata(BaseModel):
    bankName: str = Field(default="Detected Bank", description="Official name of the bank")
    accountHolder: str = Field(default="Account Holder", description="Full name of primary account holder")
    accountNumber: str = Field(default="N/A", description="Complete account number without spaces")
    ifscCode: Optional[str] = Field(default=None, description="11-character Indian Financial System Code")
    micrCode: Optional[str] = Field(default=None, description="9-digit MICR code")
    accountType: Optional[str] = Field(default="Savings Account", description="Savings, Current, Overdraft, etc.")
    address: Optional[str] = Field(default=None, description="Customer or branch address if listed")
    branch: Optional[str] = Field(default=None, description="Bank branch name or location")
    panNumber: Optional[str] = Field(default=None, description="10-digit PAN number")
    cifNumber: Optional[str] = Field(default=None, description="Customer ID / CIF / CRN")
    statementPeriod: Optional[StatementPeriod] = None
    openingBalance: Optional[str] = None
    closingBalance: Optional[str] = None