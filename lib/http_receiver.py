
from pprint import pprint
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET
import logging
import json
import re
import time
import sqlite3

from util import Identity


class HttpReceiver(Resource):

    def __init__(self, data_manager):
        self.data_manager = data_manager

    def render_GET(self, request):
        print "GOT ", request
        if 'op' in request.args:
            if request.args['op'][0] == "name_value_update":
                logging.info("HttpReceiver: Name value update.")

                if 'name' in request.args and 'value' in request.args:
                    
                    print request
                    identity = request.args['identity'][0] if 'identity' in request.args else 'foo'
                    
                    # Parse the name into '<tablename>.<column>'
                    
                    name = request.args['name'][0]

                    m = re.search(r'^([\w_]+)\.([\w_]+)$', name)
                    if m == None:
                        logging.info("HttpReciever: Invalid name in name value update: '%s'", name)
                        return ''

                    tablename = m.group(1)
                    columnname = m.group(2)
                    value = request.args['value'][0]

                    table = { 'tablename' : tablename, 
                              'column_names' : [ 'identity', columnname ],
                              'column_types' : [ 'varchar', 'varchar' ],
                              'rows' : [ [ identity, value ] ]
                            }
                    self.data_manager.sql_backend_writer.async_add_table_data(table, identity, persist=True, update=True)

        return ''

    def render_POST(self, request):
        #pprint(request.__dict__)

        newdata = request.content.getvalue()
        #print "Received POST:"
        #print newdata

        if 'op' in request.args:
            if request.args['op'][0] == "get_table_list":
                logging.info("HttpReceiver: Got request for table list.")
                response_meta = { "response_op" : "tables_list", "identity" : Identity.get_identity() }
                response_data = self.data_manager.get_table_list()
                return json.dumps(response_meta) + json.dumps(response_data)
            if request.args['op'][0] == "add_table_data":
                logging.info("HttpReceiver: Add table data.")
                response_meta = { "response_op" : "ok", "identity" : Identity.get_identity() }
                persist = True if "persist" in obj and obj["persist"] else False
                try:
                    obj = json.loads(newdata)
                    self.data_manager.sql_backend_writer.async_add_table_data(obj["table"], obj["identity"], persist=persist)
                except Exception as e:
                    response_meta = { "response_op" : "error", "identity" : Identity.get_identity(), "error_message" : str(e) }
                
                return json.dumps(response_meta)
            if request.args['op'][0] == "query":

                obj = dict()
                try:
                    obj = json.loads(newdata)
                except Exception as e:
                    response_meta = { "response_op" : "error", "identity" : Identity.get_identity(), "error_message" : str(e) }

                if 'sql' not in obj:
                    response_meta = { "response_op" : "error", "error_message" : "Must specify 'sql' param", "identity" : Identity.get_identity() }
                    logging.info("HttpReceiver: Query received with no sql.")
                    return json.dumps(response_meta)

                try:
                    self.process_sql_statement(obj["sql"], request)
                    return NOT_DONE_YET
                except Exception as e:
                    logging.error('HttpReceiver: Error processing sql: %s: %s', str(e), obj["sql"])
                    response_meta = { "response_op" : "error", "error_message" : str(e), "identity" : Identity.get_identity() }
                    return json.dumps(response_meta)
                
                return json.dumps(result)

        response_meta = { "response_op" : "error", "identity" : Identity.get_identity(), "error_message" : "Unrecognized operation" }
        return json.dumps(response_meta)


    def process_sql_statement(self, sql_statement, request):

        query_id = self.data_manager.get_query_id()

        query_start = time.time()

        logging.info("HttpReceiver: (%d) SQL received: %s", query_id, sql_statement.rstrip())

        if not sqlite3.complete_statement(sql_statement):

            # Try adding a semicolon at the end.
            sql_statement = sql_statement + ";"

            if not sqlite3.complete_statement(sql_statement):
                
                response_meta = { "response_op" : "error", 
                                  "error_message" : "Incomplete sql statement",
                                  "identity" : Identity.get_identity() }
                request.write(json.dumps(response_meta))
                request.finish()
                return

            # else it is now a complete statement

        d = self.data_manager.async_validate_and_route_query(sql_statement, query_id)

        d.addCallback(self.sql_complete_callback, query_id, query_start, request)

    def sql_complete_callback(self, result, query_id, query_start, request):
        """
        Called when a sql statement completes.
        """

        # To start just our identity.

        # To start just our identity.
        response_meta = { "identity" : Identity.get_identity() }

        retval = result["retval"]
        error_message = ''
        if "error_message" in result:
                error_message = str(result["error_message"])

        # Success?

        if retval == 0 and "data" in result:

            response_meta["response_op"] = "result_table"
            response_meta["table"] = result["data"]

            if "max_widths" in result:
                response_meta["max_widths"] = result["max_widths"]

        # Or error?

        if retval != 0:
            logging.info("HttpReceiver: (%d) SQL error: %s", query_id, error_message)

            response_meta["response_op"] = "error"
            response_meta["error_message"] = error_message

        else:
            logging.info("HttpReceiver: (%d) SQL completed (%.02f seconds)", query_id, time.time() - query_start)

        # Send the response!

        request.write(json.dumps(response_meta))
        request.finish()
