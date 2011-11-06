from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
from SocketServer import ThreadingMixIn
import multiprocessing as mp
import threading
import sys
import json
import itertools
from optparse import OptionParser
import time
import logging
import os

def f(x):
    a = sum(xrange(1, x))
    return '-%d-' % x

def eval_task(func, args, kwargs):
    try:
        return (True, f(*args, **kwargs))
    except Exception, e:
        return (False, '%s %s' % (type(e), str(e)))

def worker_loop(inq, outq):
    while True:
        job_id, func, args, kwargs = inq.get()
        logging.debug('worker %s starting task %d: %s' % (os.getpid(), job_id, str((func, args, kwargs))))
        success, result = eval_task(func, args, kwargs)
        logging.debug('worker %s completed task %d: %s' % (os.getpid(), job_id, str((success, result))))
        outq.put((job_id, success, result))

class Pool(threading.Thread):
    def __init__(self, num_workers):
        threading.Thread.__init__(self)
        self.lock = threading.Lock()
        self.daemon = True

        self.num_workers = num_workers
        self.job_counter = 0
        self.callbacks = {}

        self.outq = mp.Queue()
        self.inq = mp.Queue()
        self.workers = [self.make_worker() for i in range(self.num_workers)]

    def make_worker(self):
        w = mp.Process(target=worker_loop, args=[self.outq, self.inq])
        w.start()
        return w

    def apply_async(self, callback, func, args=[], kwargs={}, time_limit=None):
        job_id = self.new_job(callback)
        logging.debug('new task %d: %s' % (job_id, str((func, args, kwargs, time_limit))))
        self.outq.put((job_id, func, args, kwargs))

    def apply(self, func, args=[], kwargs={}, time_limit=None):
        ans = []
        def cb(*args):
            with self.lock:
                ans.append(args)
        self.apply_async(cb, func, args, kwargs, time_limit)
        while not ans:
            time.sleep(.01)
        return ans[0]

    def run(self):
        while True:
            job_id, success, result = self.inq.get()
            status = {True: 'success', False: 'exception'}[success]
            with self.lock:
                callback = self.callbacks[job_id]
                del self.callbacks[job_id]
            print '<<', job_id, result
            callback(status, result)

    def new_job(self, callback):
        with self.lock:
            job_id = self.job_counter
            self.job_counter += 1
            self.callbacks[job_id] = callback
            return job_id

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    pass

class TaskQueueHTTPGateway(threading.Thread):
    def __init__(self, port, pool):
        threading.Thread.__init__(self)
        self.server = ThreadingHTTPServer(('', port), TaskRequestHandler)
        self.server.pool = pool

    def run(self):
        self.server.serve_forever()

    def terminate(self):
        self.server.shutdown()

class TaskRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            func, args, kwargs, time_limit = self.parse_args()
        except Exception, e:
            self.send_error(*e.args)
            return

        status, result = self.server.pool.apply(func, args, kwargs, time_limit)

        self.send_response(200)
        self.send_header('Content-Type', 'text/json')
        self.end_headers()
        self.wfile.write(json.dumps({'status': status, 'result': result}))

    def parse_args(self):
        try:
            length = int(self.headers.dict['content-length'])
        except KeyError:
            raise Exception(400, 'content length required')

        raw_payload = self.rfile.read(length)

        try:
            payload = json.loads(raw_payload)
        except ValueError:
            raise Exception(400, 'invalid json body')

        try:
            func = payload['method']
            args = payload.get('args', [])
            kwargs = payload.get('kwargs', {})
            time_limit = payload.get('time_limit')
        except KeyError:
            raise Exception(400, 'missing required arguments')

        return func, args, kwargs, time_limit

def parse_options():
    parser = OptionParser()
    parser.add_option("-p", "--port", dest="port", type='int', default=9690)
    parser.add_option("-w", "--workers", dest="num_workers", type='int', default=3)
    (options, args) = parser.parse_args()
    return options

if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)

    opts = parse_options()

    pool = Pool(opts.num_workers)
    pool.start()
    logging.info('process pool started with %d workers' % opts.num_workers)

    # also proof-of-concept'ed with tornado, but requests were getting dropped
    # under high load
    gw = TaskQueueHTTPGateway(opts.port, pool)
    gw.start()
    logging.info('gateway started on port %d' % opts.port)

    try:
        while True:
            time.sleep(.01) #yield thread
    except KeyboardInterrupt:
        logging.info('shutting down...')
        gw.terminate()
