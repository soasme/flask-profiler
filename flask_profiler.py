# -*- coding: utf-8 -*-

import json
import pstats
import logging
try:
    from cProfile import Profile
except ImportError:
    from profile import Profile

from flask import current_app, jsonify, g

class FlaskProfiler(object):

    def __init__(self, app=None):
        self.app = app
        self.loggers = []
        self.profilers = []
        self.log_handlers = []
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        app.config.setdefault('PROFILE_FUNCTION', False)
        app.config.setdefault('PROFILE_SQLALCHEMY', False)
        app.config.setdefault('PROFILE_HTML_PLACEHOLDER', None)
        app.config.setdefault('PROFILE_JSONIFY_KEY', None)

        if app.config.get('PROFILE_FUNCTION'):
            self.add_profiler(profile_function())

        if app.config.get('PROFILE_SQLALCHEMY'):
            self.add_profiler(profile_sqlalchemy())

        if app.config.get('PROFILE_HTML_PLACEHOLDER'):
            placeholder = app.config.get('PROFILE_HTML_PLACEHOLDER')
            self.add_log_handler(html_body_log_handler(placeholder))

        if app.config.get('PROFILE_JSONIFY_KEY'):
            jsonify_key = app.config.get('PROFILE_JSONIFY_KEY')
            self.add_log_handler(jsonify_log_handler(jsonify_key))

    def add_profiler(self, profiler):
        self.profilers.append(profiler)
        if profiler.get('before_request'):
            self.app.before_request(profiler['before_request'])
        if profiler.get('after_request'):
            self.app.after_request(profiler['after_request'])
        if profiler.get('logger'):
            self.loggers.append(profiler['logger'])
        return profiler

    def add_log_handler(self, handler):
        self.log_handlers.append(handler)
        for logger in self.loggers:
            logger.addHandler(handler['handler'])
            if handler.get('before_request'):
                self.app.before_request(handler['before_request'])
            if handler.get('after_request'):
                self.app.after_request(handler['after_request'])
        return handler

def get_func_calls_from_stats(stats):
    func_calls = []
    if not stats:
        return func_calls

    for func in stats.sort_stats(1).fcn_list:
        info = stats.stats[func]
        stat = {}

        # Number of calls
        if info[0] != info[1]:
            stat['ncalls'] = "%d/%d" % (info[1], info[0])
        else:
            stat['ncalls'] = info[1]

        # Total time
        stat['tottime'] = info[2] * 1000

        # Time per call
        if info[1]:
            stat['percall'] = info[2] * 1000 / info[1]
        else:
            stat['percall'] = 0

        # Cumulative time spent in this and all subfunctions
        stat['cumtime'] = info[3] * 1000

        # Cumulative time per primitive call
        if info[0]:
            stat['percall_cum'] = info[3] * 1000 / info[0]
        else:
            stat['percall_cum'] = 0

        # Filename
        stat['filename'] = pstats.func_std_string(func)

        func_calls.append(stat)

    return func_calls


def profile_function():
    logger_name = 'flask.profiler.function'
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)

    def enable_function_profiler():
        g.flask_profiler_function = Profile()
        g.flask_profiler_function.enable()

    def disable_function_profiler(resp):
        g.flask_profiler_function.disable()
        stats = pstats.Stats(g.flask_profiler_function)
        func_calls = get_func_calls_from_stats(stats)
        logger.info('%s,%s,%s,%s,%s,%s',
                    'Calls',
                    'Total Time(ms)',
                    'Per Call(ms)',
                    'Cumulative Time(ms)',
                    'Per Call (ms)',
                    'Function')
        for call in func_calls:
            logger.info(
                '%s %.4f %.4f %.4f %.4f %s',
                call['ncalls'],
                call['tottime'],
                call['percall'],
                call['cumtime'],
                call['percall_cum'],
                call['filename'],
            )
        return resp

    return dict(
        logger=logger,
        before_request=enable_function_profiler,
        after_request=disable_function_profiler,
    )


def profile_sqlalchemy():
    from flask_sqlalchemy import get_debug_queries

    logger_name = 'flask.profiler.sqlalchemy_queries'
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)

    def profile_sqlalchemy_queries(resp):
        queries = get_debug_queries()
        logger.info('SQLAlchemy sql quantity: %d', len(queries))
        logger.info('SQLAlchemy total elapsed: %.4f ms', sum(q.duration * 1000.0 for q in queries))
        for query in queries:
            logger.info('%s %s %.4f "%s" "%s"',
                        query.start_time,
                        query.end_time,
                        query.duration * 1000.0,
                        query.statement,
                        query.parameters,)
        return resp

    return dict(
        logger=logger,
        after_request=profile_sqlalchemy_queries,
    )

def jsonify_log_handler(jsonify_key):
    class JSONifyLogHandler(logging.handlers.BufferingHandler):
        def shouldFlush(self, record):
            return False
        def flush(self):
            self.acquire()
            try:
                records = [self.format(record) for record in self.buffer]
                self.buffer = []
                return records
            finally:
                self.release()
    handler = JSONifyLogHandler(capacity=99999999)

    def merge_into_jsonified_output(resp):
        if resp.status_code == 200 and resp.headers['Content-Type'].startswith('application/json'):
            data = json.loads(resp.data)
            if isinstance(data, dict) and jsonify_key and jsonify_key not in data:
                data[jsonify_key] = handler.flush()
            resp.response = jsonify(data).response
            resp.content_length = sum(map(len, resp.response))
        return resp

    return dict(
        handler=handler,
        after_request=merge_into_jsonified_output,
    )

def html_body_log_handler(placeholder):
    class HTMLBodyLogHandler(logging.handlers.BufferingHandler):

        def shouldFlush(self, record):
            return False

        def flush(self):
            self.acquire()
            try:
                # TODO: use jinja2 to beautify html
                html = '<div style="font-size:0.7rem; background: white; font-family: Monaco, Consolas, Menlo, Courier, monospace; z-index: 10000; padding: 1rem;">'
                for record in self.buffer:
                    html += '<p>%s</p>\n' % self.format(record)
                html += '</div>'
                self.buffer = []
                return html
            finally:
                self.release()

    handler = HTMLBodyLogHandler(capacity=99999999)

    def render_logs_to_html_body(resp):
        if resp.status_code == 200 and resp.headers['Content-Type'].startswith('text/html'):
            html = resp.data.decode(resp.charset)
            report = handler.flush()
            html = html.replace('<!-- %s -->' % placeholder, report)
            html = html.encode(resp.charset)
            resp.response = [html]
            resp.content_length = len(html)
        return resp

    return dict(
        handler=handler,
        after_request=render_logs_to_html_body,
    )