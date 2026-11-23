"""Owned financial backup with atomic restore into an empty ledger."""
import json
import secrets
from datetime import date, datetime
from decimal import Decimal
from flask import Blueprint, render_template, request, Response, flash, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import Date, DateTime, Numeric, Integer, Boolean, String
from sqlalchemy.exc import SQLAlchemyError
from . import models
from .extensions import db
from .forms import BaseForm

bp = Blueprint('backup', __name__, url_prefix='/backup')
NAMES = ['Category','Account','RecurringExpense','CreditCardBill','SavingsGoal','Investment',
         'Commitment','Transfer','Transaction','RecurringOccurrence','CommitmentOccurrence',
         'GoalContribution','CommitmentPayment','InvestmentRedemption']


def backup_models():
    return [getattr(models, name) for name in NAMES if hasattr(models, name)]


def financial_backup(user_id):
    tables = {}
    owned_ids = {}
    for model in backup_models():
        table = model.__table__
        query = db.select(model)
        if 'user_id' in table.c:
            query = query.where(table.c.user_id == user_id)
        else:
            parent = next((fk for col in table.c for fk in col.foreign_keys if fk.column.table.name in owned_ids), None)
            if parent is None:
                raise ValueError('Backup relationship is unavailable.')
            query = query.where(parent.parent.in_(owned_ids[parent.column.table.name]))
        rows = db.session.scalars(query).all()
        owned_ids[table.name] = [row.id for row in rows]
        tables[model.__name__] = [{col.name: encode(getattr(row,col.name)) for col in table.c if col.name != 'user_id'} for row in rows]
    return {'format':'xpense-financial-backup','version':1,'currency':current_user.currency,'tables':tables}


def encode(value):
    if isinstance(value,(date,datetime)):
        return value.isoformat()
    if isinstance(value,Decimal):
        return str(value)
    return value


def decode(col, value):
    if value is None:
        if not col.nullable and col.default is None:
            raise ValueError(f'{col.name} is required.')
        return None
    if isinstance(col.type,Boolean):
        if type(value) is not bool: raise ValueError('Invalid boolean.')
    elif isinstance(col.type,Integer):
        if type(value) is not int: raise ValueError('Invalid integer.')
        if abs(value) > 9223372036854775807: raise ValueError('Integer exceeds storage bounds.')
    elif isinstance(col.type,DateTime): value = datetime.fromisoformat(value)
    elif isinstance(col.type,Date): value = date.fromisoformat(value)
    elif isinstance(col.type,Numeric):
        value = Decimal(value)
        if not value.is_finite(): raise ValueError('Invalid decimal.')
    elif isinstance(col.type,String):
        if not isinstance(value,str) or col.type.length and len(value)>col.type.length: raise ValueError('Invalid text.')
    return value


def restore_backup(payload,user_id):
    if not isinstance(payload,dict) or payload.get('format')!='xpense-financial-backup' or payload.get('version')!=1:
        raise ValueError('Choose a version 1 Xpense financial backup.')
    if payload.get('currency') != current_user.currency:
        raise ValueError('The profile currency must match the backup currency.')
    tables=payload.get('tables')
    if not isinstance(tables,dict) or set(tables)-set(NAMES): raise ValueError('Unknown backup tables.')
    catalog=backup_models()
    # Lock the user row before the emptiness check to serialize simultaneous restores.
    db.session.execute(db.update(models.User).where(models.User.id==user_id).values(name=models.User.name))
    for model in catalog:
        if model is models.Category or 'user_id' not in model.__table__.c: continue
        if db.session.scalar(db.select(model.id).where(model.user_id==user_id).limit(1)):
            raise ValueError('Restore requires an empty financial ledger. Existing records are never replaced.')
    mappings={}; deferred=[]; total=0
    for model in catalog:
        rows=tables.get(model.__name__,[])
        if not isinstance(rows,list): raise ValueError('Invalid backup rows.')
        total+=len(rows)
        if total>10000: raise ValueError('Backup exceeds 10,000 records.')
        table=model.__table__; mappings[table.name]={}
        for data in rows:
            if not isinstance(data,dict) or set(data)-set(table.c.keys()) or 'user_id' in data:
                raise ValueError('Invalid backup fields.')
            old_id=data.get('id')
            if type(old_id) is not int or old_id<=0 or old_id in mappings[table.name]: raise ValueError('Invalid record identity.')
            if model is models.Category:
                existing=db.session.scalar(db.select(model).where(model.user_id==user_id,model.name==data.get('name')))
                if existing:
                    mappings[table.name][old_id]=existing.id
                    continue
            row=model()
            if 'user_id' in table.c: row.user_id=user_id
            for col in table.c:
                if col.name in ('id','user_id'):continue
                value=decode(col,data.get(col.name))
                fk=next(iter(col.foreign_keys),None)
                if fk and value is not None:
                    parent=fk.column.table.name
                    if parent==table.name or parent not in mappings:
                        if not col.nullable: raise ValueError('Unsupported backup relationship order.')
                        deferred.append((row,col.name,parent,value));value=None
                    else:
                        if value not in mappings[parent]: raise ValueError('Backup contains a missing parent record.')
                        value=mappings[parent][value]
                if col.unique and isinstance(value,str):value=secrets.token_hex(min(16,(col.type.length or 32)//2))
                setattr(row,col.name,value)
            db.session.add(row);db.session.flush();mappings[table.name][old_id]=row.id
    for row,column,parent,old_id in deferred:
        if old_id not in mappings.get(parent,{}):raise ValueError('Backup contains a missing linked record.')
        setattr(row,column,mappings[parent][old_id])
    db.session.flush()
    return total


@bp.get('/download')
@login_required
def download():
    payload=financial_backup(current_user.id)
    return Response(json.dumps(payload,indent=2),mimetype='application/json',headers={'Content-Disposition':'attachment; filename=xpense-financial-backup.json','Cache-Control':'no-store'})


@bp.route('/',methods=['GET','POST'])
@login_required
def index():
    error=None
    if request.method=='POST' and BaseForm().validate_on_submit():
        try:
            upload=request.files.get('backup')
            if upload is None:raise ValueError('Choose a backup file.')
            payload=json.loads(upload.read().decode('utf-8-sig'))
            count=restore_backup(payload,current_user.id)
            db.session.commit()
            flash(f'Restored {count} financial records.','success')
            return redirect(url_for('reports.dashboard'))
        except (ValueError,TypeError,ArithmeticError,SQLAlchemyError) as exc:
            db.session.rollback()
            error=str(exc) if isinstance(exc,ValueError) else 'The backup could not be restored. No records were saved.'
    return render_template('backup.html',error=error)
