# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2010 Tiny SPRL (<http://tiny.be>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from openerp import models, fields, api, _
import logging
import base64
import csv
import re
import uuid
from datetime import date ,datetime
from dateutil.relativedelta import relativedelta
from openerp.exceptions import ValidationError
from openerp.tools.safe_eval import safe_eval
import xlrd

_logger = logging.getLogger(__name__)


class product_import(models.Model):
    _name = 'product.import'
    _description = 'product import'

    state = fields.Selection(
        [('draft', 'draft'), 
         ('process', 'process'), 
         ('updated', 'updated'), 
         ('created', 'created'), 
         ('done', 'done'),
         ('cancel', 'cancel')],
        string='state',
        default='draft',
        required=True,
    )

    config_id = fields.Many2one(
        'product.import.config',
        string='Config',
        required=True,
    )
    
    serialized_values_ids = fields.One2many(
        'product.import.serialized_values',
        'import_id',
        string='Serialized',
    )
    
    create_values_ids = fields.One2many(
        'product.import.field_values',
        'import_id',
        string='Create',
        domain=[('event','=','create'), ]
    )

    write_values_ids = fields.One2many(
        'product.import.field_values',
        'import_id',
        string='Update',
        domain=[('event','=','write')]
    )
    error = fields.Text(
        string='Error',
    )
    created = fields.Boolean(
        string='Create execute',
    )
    updated = fields.Boolean(
        string='Update execute',
    )
    file_ids = fields.Many2many('ir.attachment', string="File")

    active = fields.Boolean(
        string='Active',
        default = True
    )

    product_ids = fields.Many2many(
        'product.product',
        string='Products',
    )

    @api.model
    def cast_field_value(self,field, value):
        try:
            if field.ttype == 'float':
                return float(value)
            if field.ttype == 'integer':
                return int(value)
            if field.ttype == 'boolean':
                return bool(value)
            if field.ttype in ['char','text','html']:
                if type(value) == unicode:
                    return value.encode('utf-8','ignore')
                return str(value)
            if field.ttype =='many2one':
                if type(value) == unicode:
                    value = value.encode('utf-8','ignore')
                return str(value).rstrip().lstrip()

            if field.ttype in ['one2many']:
                if type(value) == unicode:
                    return value.encode('utf-8','ignore')
                v = [x.rstrip().lstrip() for x in ','.split(value)]
                return ','.join(v)
            
        except :
            return False

    @api.multi
    def action_list(self):
        self.ensure_one()
        view_id = self.env.ref('product_import.product_import_tree') 
        view = { 
            'name':"Product",
            'view_mode': 'tree',
            'view_id': view_id.id,
            'view_type': 'form',
            'res_model': 'product.product',
            'type': 'ir.actions.act_window',
            'nodestroy': True,
            'target': 'self',
            'domain': "[('id','in',%r)]"%(self.product_ids.ids),
        }
        return view


    @api.multi
    def action_import(self):
        self.ensure_one()
        view_id = self.env.ref('product_import.product_import_form')
        
        return {
            'name': _("Import ") ,
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'product.import',
            'res_id': self.id,
            'view_id': view_id.id,
            'type': 'ir.actions.act_window',
            'context': {'default_config_id':self.id},
            'nodestroy': True,
            'domain': [],
        }



    @api.model
    def prepare_field_value(self,field, value):
        if field.ttype == 'float':
            return float(value)
        if field.ttype == 'integer':
            return int(value)
        if field.ttype == 'boolean':
            return bool(value)
        if field.ttype in ['char','text','html']:
            if type(value) == unicode:
                return value.encode('utf-8','ignore')
            return str(value)
        if field.ttype =='many2one':
            name = self.env[field.relation].name_search(value)
            if len(name):
                return name[0][0]            
            else:
                new_id = self.env[field.relation].create({'name':value})
                return new_id.id

        if field.ttype in ['one2many']:
            vals = ','.split(value)
            res = []
            for val in vals:
                if len(name):
                    res.append(name[0][0])            
                else:
                    new_id = self.env[field.relation].create({'name':value})
                    res.append(new_id.id)            
            return res
        
        return value

    @api.one 
    def update_data(self):
        product_ids = [] 
        error_lines = []

        product_vals = {}
        supplier_vals = {}
        packaging_vals = {}
        product_list = {}
        for row in self.write_values_ids:
            if row.field_id.model_id.model =='product.supplierinfo':
                if row.product_id.id not in supplier_vals:
                    supplier_vals[row.product_id.id] = {}
                supplier_vals[row.product_id.id][row.field_id.name]=self.prepare_field_value(row.field_id,row.parsed_value)

            if row.field_id.model_id.model =='product.packaging':
                if row.product_id.id not in packaging_vals:
                    packaging_vals[row.product_id.id] = {}
                packaging_vals[row.product_id.id][row.field_id.name]=self.prepare_field_value(row.field_id,row.parsed_value)


            else:
                if row.product_id.id not in product_vals:
                    product_vals[row.product_id.id] = {}
                    product_list[row.product_id.id] = row.product_id
                product_vals[row.product_id.id][row.field_id.name]=self.prepare_field_value(row.field_id,row.parsed_value)

        for product in product_vals:
            try:
                if product in packaging_vals and 'ean' in packaging_vals[product] :
                    pack_id = 0
                    for packaging_id in product_list[row.product_id.id].packaging_ids:
                        if packaging_id.ean == packaging_vals[product]['ean']:
                            pack_id = packaging_id.id
                    if pack_id !=0:
                        product_vals[product]['packaging_ids']=[(1,pack_id,packaging_vals[product])]
                    else:
                        product_vals[product]['packaging_ids']=[(0,0,packaging_vals[product])]

                if 'update_supplierinfo' in self.env.context:
                    if 'product_code' not in supplier_vals:
                        supplier_vals[product]['product_code']=product_list[product].default_code
                    if 'product_name' not in supplier_vals:
                        supplier_vals[product]['product_name']=product_list[product].name
                    supplier_vals[product]['name']=self.config_id.partner_id.id
                    supplierinfo_id = 0
                    for seller_id in product_list[row.product_id.id].seller_ids:
                        if seller_id.name.id == self.config_id.partner_id.id:
                            supplierinfo_id = seller_id.id
                    if supplierinfo_id!=0:
                        product_vals[product]['seller_ids']=[(1,supplierinfo_id,supplier_vals[product])]
                    else:
                        product_vals[product]['seller_ids']=[(0,0,supplier_vals[product])]


                _logger.info(product_vals[product])
                product_list[product].write(product_vals[product])

                product_ids.append(product)
            except Exception as e:
                        error_lines.append('%r'%e)
                        for line in product_vals[product]:
                            error_lines.append('%r : %r'%(line, product_vals[product][line]))
                        error_lines.append('-------------------')



        _logger.info([(4,x) for x in product_ids])
        self.product_ids= [(4,x) for x in product_ids]
        self.updated=True
        self.state="updated"
        self.error = '\n'.join(error_lines)

    @api.one 
    def create_data(self):
        product_vals = {}
        supplier_vals = {}
        error_lines = []
        product_ids = [] 


        for row in self.create_values_ids:
            if row.field_id.model_id.model =='product.supplierinfo':
                if row.unic not in supplier_vals:
                    supplier_vals[row.unic] = {}
                supplier_vals[row.unic][row.field_id.name]=self.prepare_field_value(row.field_id,row.parsed_value)

            else:
                if row.unic not in product_vals:
                    product_vals[row.unic] = {}
                product_vals[row.unic][row.field_id.name]=self.prepare_field_value(row.field_id,row.parsed_value)
        for product in product_vals:
            try:
                if 'categ_id' not in product_vals[product] and len(self.config_id.default_categ_id):
                    product_vals[product]['categ_id'] = self.config_id.default_categ_id.id

                if self.config_id.supplierinfo:
                    #supplier_vals[product]['product_tmpl_id']=new_product.product_tmpl_id.id
                    supplier_vals[product]['name']=self.config_id.partner_id.id
                    if 'product_code' not in supplier_vals[product]:
                        supplier_vals[product]['product_code']=product_vals[product]['default_code']
                    if 'product_name' not in supplier_vals[product]:
                        supplier_vals[product]['product_name']=product_vals[product]['name']
                    product_vals[product]['seller_ids']=[(0,0,supplier_vals[product])]

                    #_logger.info('supplier_vals %r'%supplier_vals[product])
                    #self.env['product.supplierinfo'].create(supplier_vals[product])

                new_product = self.env['product.product'].create(product_vals[product])
                
                product_ids.append(new_product.id)


            except Exception as e:

                error_lines.append('%r'%e)
                for line in product_vals[product]:
                    error_lines.append('%r : %r'%(line, product_vals[product][line]))
                for line in supplier_vals[product]:
                    error_lines.append('%r : %r'%(line, supplier_vals[product][line]))
                error_lines.append('-------------------')

        self.created=True
        self.state="created"
        _logger.info([(4,x) for x in product_ids])
        self.product_ids= [(4,x) for x in product_ids]
        self.error =  self.error + '\n'.join(error_lines)
    @api.one 
    def action_done(self):
        self.state="done"
    @api.one 
    def action_restart(self):
        self.error =  ''
        self.state="draft"

    @api.one 
    def action_process(self):
        self.write_values_ids.unlink()
        self.create_values_ids.unlink()
        self.error = ''
        #self.serialized_values_ids.unlink()
        if self.config_id.file_format == 'csv':
            self.process_csv()
        elif self.config_id.file_format == 'xls':
            self.process_xls()
        self.state="process"
    
    @api.one 
    def process_xls(self):
        for file in self.file_ids:
            book = xlrd.open_workbook(file_contents=base64.decodestring(file.datas))
            sheet_names = book.sheet_names()
            sheet_names = [x.lower() for x in sheet_names ]
            for sheet in  self.config_id.sheet_ids:
                _logger.info(sheet.name.lower())
                
                if sheet.name.lower() in sheet_names  or sheet.name.isnumeric():

                    index = int(sheet.name)-1 if sheet.name.isnumeric() else sheet_names.index(sheet.name.lower())
                    sheet=book.sheet_by_index(index)
                    for i in range(sheet.nrows):

                        if i < self.config_id.start_line:
                            continue
                        r = [cell.value for cell in sheet.row(i)] 
                        
                        _logger.info(r)
                        self.process_row(r)

    @api.one
    def process_csv(self):
        for file in self.file_ids:
            line=0
            atach_content = base64.decodestring(file.datas)
            spamreader = csv.reader(atach_content.split(
                '\n'), delimiter=",", quotechar='"')
            for row in spamreader:
                line += 1
                if line < self.config_id.start_line:
                    continue
                self.process_row(row)

    @api.one
    def process_row(self,row):
        res = {}
        leaf = [('active','=',True)]
        if self.config_id.match_supplier:
            leaf.append(('seller_ids.name','=',self.config_id.partner_id.id))
        rowlen = len(row)
        for field in self.config_id.field_ids:
            if rowlen < field.column:
                return
            if len(field.preprocessed_id):
                v = field.preprocessed_id.validate_value(self.config_id,field.column,row)
            else :
                v = self.cast_field_value(field.field_def_id.field_id, row[field.column-1])
            if field.required == True and (v == None or v == '' or v == 0):
                return 

            res[field.field_def_id.field_id.id] = v
            
            if field.match:
                if field.field_def_id.field_id.model_id.model =='product.supplierinfo':
                    leaf.append(("seller_ids.%s"%field.field_def_id.field_id.name,'=',v))
                else:
                    leaf.append((field.field_def_id.field_id.name,'=',v))
        
        product = self.find_product(leaf)


        if len(product):
            #self.add_serialized_line(res,product.id)
            self.add_update_line(product,res)
        else:
            newuuid= uuid.uuid4()
            for default_value in self.config_id.default_value_ids:
                v = self.cast_field_value(default_value.field_id, default_value.field_value)
                res[default_value.field_id.id] = v
            #self.add_serialized_line(res,False,newuuid)
            self.add_create_line(newuuid,res)



    @api.one
    def add_serialized_line(self,res_vals,product_id=False,new_id=False):
        for field in res_vals:
            self.env['product.import.serialized_values'].create(
                {
                    'import_id':self.id,
                    'data':res_vals,
                    'product_id':product_id,
                    'unic':new_id
                }
            )

    @api.one
    def add_update_line(self,product_id,res_vals):
        for field in res_vals:
            self.env['product.import.field_values'].create(
                {
                    'import_id':self.id,
                    'event':'write',
                    'field_id':field,
                    'parsed_value':str(res_vals[field]),
                    'product_id':product_id.id
                }
            )

    @api.one
    def add_create_line(self,newuuid,res_vals):
        for field in res_vals:
            self.env['product.import.field_values'].create(
                {
                    'import_id':self.id,
                    'event':'create',
                    'field_id':field,
                    'parsed_value':str(res_vals[field]),
                    'unic':newuuid
                }
            )

    #@api.one
    @api.returns('product.product')
    def find_product(self,leaf):
        return self.env['product.product'].search(leaf,limit=1)        


class product_import_serialized_values(models.Model):

    _name = 'product.import.serialized_values'
    _description = 'product import field values'


    import_id = fields.Many2one(
        'product.import',
        string='Config',
    )

    product_id = fields.Many2one(
        'product.product',
        string='product',
    )
    unic =  fields.Char(
        string='unic',
        index = True,
    )
    data = fields.Serialized(
        string='Data',
    )
    product_id = fields.Many2one(
        'product.product',
        string='product',
    )



class product_import_field_values(models.Model):
    _name = 'product.import.field_values'
    _description = 'product import field values'

    import_id = fields.Many2one(
        'product.import',
        string='Config',
    )
    event = fields.Selection(
        [('write', 'write'), ('create', 'create')],
        string='event',
        required=True,
    )

    field_id = fields.Many2one(
        'ir.model.fields',
        string='Field',
    )
    parsed_value = fields.Char(
        string='Value',
    )
    product_id = fields.Many2one(
        'product.product',
        string='product',
    )
    unic = fields.Char(
        string='unic',
        index = True,
    )


class product_import_config(models.Model):
    _name = 'product.import.config'
    _description = 'product import config'

    name = fields.Char(
        string='',
        size=64,
        required=True,
        readonly=False,
    )
    partner_id = fields.Many2one(
        'res.partner',
        required=True,
        string='Supplier',
    )
    file_format = fields.Selection(
        [('csv', 'csv'), ('xls', 'xls'), ('xml', 'xml')],
        string='file format',
        required=True,
    )
    csv_delimiter = fields.Char(
        string='csv delimiter',
        default =','
    )
    csv_quotechar = fields.Char(
        string='csv quotechar',
        default ='"'
    )
    start_line = fields.Integer(
        string='start line',
    )
    context = fields.Char(
        string='context',
        default='{}'
    )
    sheet_ids = fields.Many2many(
        comodel_name='product.import.config.sheet',
        relation='config_sheet_rel',
        column1='config_id',
        column2='sheet_id'
    )

    field_ids = fields.One2many(
        'product.import.config.column',
        'config_id',
        string='Fields',
        required=True,
    )
    import_ids = fields.One2many(
        'product.import',
        'config_id',
        string='Imports',
    )
    default_value_ids = fields.One2many(
        'product.import.config.default_values',
        'config_id',
        string='default values',
        required=False,
    )
    supplierinfo = fields.Boolean(
        string='Add supplier info',
        default = True
    )
    match_supplier = fields.Boolean(
        string='match supplier',
        default = True
    )
    default_categ_id = fields.Many2one(
        'product.category',
        string='Default category',
    )

    active = fields.Boolean(
        string='Active',
        default = True
    )


    @api.multi
    def action_import(self):
        self.ensure_one()
        view_id = self.env.ref('product_import.product_import_form')
        
        return {
            'name': _("Import '%s'") % self.name,
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'product.import',
            'view_id': view_id.id,
            'type': 'ir.actions.act_window',
            'context': {'default_config_id':self.id},
            'nodestroy': True,
            'domain': [],
        }

class product_import_config_sheet(models.Model):
    _name = 'product.import.config.sheet'
    _description = 'product import sheet'
    name = fields.Char(
        string='Sheet',
    )

class product_import_preprocessed(models.Model):
    _name = 'product.import.preprocessed'
    _description = 'product import render function'
    def _default_code():
        return _(
            "\n# Python code. Use:\n"
            "#  -  failed = True: specify that the value is not "
            "valid.\n"
            "# You can use the following:\n"
            "#  - re: regex Python library\n"
            "#  - date: date Python library\n"
            "#  - datetime: datetime Python library\n"
            "#  - relativedelta: relativedelta Python library\n"
            "#  - self: browse_record of the current document type\n"
            "#  - config_id: config_id res\n"
            "#  - row: row data\n"
            "#  - column: column number\n"
            "# Return is a float in variable return_value"
        )

    name = fields.Char(
        string='name',
    )
    python_code = fields.Text(
        string='Function',
        default = _default_code()
    )

    @api.multi
    def _validation_eval_context(self, config_id,column,row):
        self.ensure_one()

        return {'self': self,
                'column': column,
                'row': row,
                're': re,
                'date':date ,
                'datetime':datetime,
                'relativedelta':relativedelta,
                'config_id':config_id,
                'type':type
                }

    @api.multi
    def validate_value(self, config_id,column, row, do_not_raise=False):
        """Validate the given ID number
        The method raises an openerp.exceptions.ValidationError if the eval of
        python validation code fails
        If you call with return_parts=True, then we will return the parts list
        """
        # if we call it without any record, we return value
        if not self:
            return 0

        self.ensure_one()
        self = self.sudo()

        eval_context = self._validation_eval_context(config_id,column, row)
        msg = None

        try:
            safe_eval(self.python_code,
                      eval_context,
                      mode='exec',
                      nocopy=True)

        except Exception as e:
            msg = (_(
                'Error when evaluating %s. Please check validation code.\n'
                'Error:\n%s') % (self.name, e))

        if eval_context.get('failed', False):
            msg = (_(
                "'%s' is not a valid value for '%s'.") % (
                column, ','.join(row) or ''))

        elif eval_context.get('return_value', False):
            value = eval_context.get('return_value', False)
        else:
            value = 0
        if msg:
            if do_not_raise:
                return msg
            else:
                raise ValidationError(msg)
        return value

class product_import_config_column(models.Model):
    _name = 'product.import.config.column'
    _description = 'product import fields'

    config_id = fields.Many2one(
        'product.import.config',
        string='Config',
    )
    field_def_id = fields.Many2one(
        'product.import.config.field',
        string='Field',
    )
    column = fields.Integer(
        string='column',
    )
    required = fields.Boolean(
        string='Required',
    )
    preprocessed_id = fields.Many2one(
        'product.import.preprocessed',
        string='preproces',
    )

    match = fields.Boolean(
        string='match',
    )

    @api.onchange('field_def_id')
    def _onchange_field_name(self):
        self.preprocessed_id=self.field_def_id.preprocessed_id
            

class product_import_config_field(models.Model):
    _name = 'product.import.config.field'
    _description = 'product import fields definition'

    name = fields.Char(
        string='name',
    )

    field_id = fields.Many2one(
        'ir.model.fields',
        string='Field',
    )
    preprocessed_id = fields.Many2one(
        'product.import.preprocessed',
        string='preproces',
    )


class product_import_config_default_values(models.Model):
    _name = 'product.import.config.default_values'
    _description = 'product import default_values'

    config_id = fields.Many2one(
        'product.import.config',
        string='Config',
    )
    field_id = fields.Many2one(
        'ir.model.fields',
        string='Field',
    )
    field_value = fields.Char(
        string='value',
    )
