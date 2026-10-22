import logging
import os
import tempfile
import threading
import unittest

from scanbridge.delivery import Delivery
from scanbridge.destinations.base import Destination


class FakeDestination(Destination):
    type = 'fake'

    def __init__(self, name, failing=False):
        super().__init__(name)
        self.failing = failing
        self.attempts = []  # file names tried, in order
        self.stored = {}  # file name -> data

    def store(self, filename, data):
        self.attempts.append(filename)
        if self.failing:
            raise OSError('destination is down')
        self.stored[filename] = data
        return f'/fake/{filename}'


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)  # failures are expected here, keep the output clean
        self.addCleanup(logging.disable, logging.NOTSET)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.queue = os.path.join(tmp.name, 'queue')

    def delivery(self, **dests):
        return Delivery(dests, self.queue)

    def queued(self, name):
        folder = os.path.join(self.queue, name)
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def age(self, name, filename, seconds_ago):
        path = os.path.join(self.queue, name, filename)
        stamp = os.path.getmtime(path) - seconds_ago
        os.utime(path, (stamp, stamp))

    def test_success_is_not_queued(self):
        dest = FakeDestination('a')
        self.delivery(a=dest).deliver(['a'], 'x.pdf', b'1')
        self.assertEqual(dest.stored, {'x.pdf': b'1'})
        self.assertEqual(self.queued('a'), [])

    def test_failed_file_is_queued_in_destination_folder(self):
        dest = FakeDestination('a', failing=True)
        self.delivery(a=dest).deliver(['a'], 'x.pdf', b'1')
        self.assertEqual(self.queued('a'), ['x.pdf'])
        with open(os.path.join(self.queue, 'a', 'x.pdf'), 'rb') as f:
            self.assertEqual(f.read(), b'1')

    def test_retry_stores_and_removes_file(self):
        dest = FakeDestination('a', failing=True)
        delivery = self.delivery(a=dest)
        delivery.deliver(['a'], 'x.pdf', b'1')
        dest.failing = False
        delivery.retry()
        self.assertEqual(dest.stored, {'x.pdf': b'1'})
        self.assertEqual(self.queued('a'), [])

    def test_retry_while_still_failing_keeps_file(self):
        dest = FakeDestination('a', failing=True)
        delivery = self.delivery(a=dest)
        delivery.deliver(['a'], 'x.pdf', b'1')
        delivery.retry()
        delivery.retry()
        self.assertEqual(self.queued('a'), ['x.pdf'])
        self.assertEqual(dest.attempts, ['x.pdf'] * 3)

    def test_retry_stops_at_first_failure(self):
        dest = FakeDestination('a', failing=True)
        delivery = self.delivery(a=dest)
        for n in range(3):
            delivery.deliver(['a'], f'{n}.pdf', b'x')
            self.age('a', f'{n}.pdf', 100 - n)
        del dest.attempts[:]
        delivery.retry()
        self.assertEqual(dest.attempts, ['0.pdf'])
        self.assertEqual(self.queued('a'), ['0.pdf', '1.pdf', '2.pdf'])

    def test_retry_oldest_first(self):
        dest = FakeDestination('a', failing=True)
        delivery = self.delivery(a=dest)
        for name, age in (('new.pdf', 10), ('oldest.pdf', 300), ('middle.pdf', 100)):
            delivery.deliver(['a'], name, b'x')
            self.age('a', name, age)
        dest.failing = False
        del dest.attempts[:]
        delivery.retry()
        self.assertEqual(dest.attempts, ['oldest.pdf', 'middle.pdf', 'new.pdf'])
        self.assertEqual(self.queued('a'), [])

    def test_pending_orders_by_modification_time(self):
        delivery = self.delivery(a=FakeDestination('a', failing=True))
        for name, age in (('b.pdf', 10), ('a.pdf', 20)):
            delivery.deliver(['a'], name, b'x')
            self.age('a', name, age)
        self.assertEqual([os.path.basename(p) for p in delivery.pending('a')], ['a.pdf', 'b.pdf'])

    def test_pending_ignores_part_files_and_missing_folder(self):
        delivery = self.delivery(a=FakeDestination('a'))
        self.assertEqual(delivery.pending('a'), [])
        os.makedirs(os.path.join(self.queue, 'a'))
        for name in ('x.pdf', 'y.pdf.part'):
            with open(os.path.join(self.queue, 'a', name), 'wb') as f:
                f.write(b'x')
        self.assertEqual([os.path.basename(p) for p in delivery.pending('a')], ['x.pdf'])

    def test_one_failing_destination_only_queues_for_that_one(self):
        good, bad = FakeDestination('good'), FakeDestination('bad', failing=True)
        delivery = self.delivery(good=good, bad=bad)
        delivery.deliver(['bad', 'good'], 'x.pdf', b'1')
        self.assertEqual(good.stored, {'x.pdf': b'1'})
        self.assertEqual(self.queued('good'), [])
        self.assertEqual(self.queued('bad'), ['x.pdf'])
        bad.failing = False
        delivery.retry()
        self.assertEqual(bad.stored, {'x.pdf': b'1'})
        self.assertEqual(good.attempts, ['x.pdf'])  # not delivered twice
        self.assertEqual(self.queued('bad'), [])

    def test_failing_destination_does_not_block_others_on_retry(self):
        a, b = FakeDestination('a', failing=True), FakeDestination('b', failing=True)
        delivery = self.delivery(a=a, b=b)
        delivery.deliver(['a', 'b'], 'x.pdf', b'1')
        b.failing = False
        delivery.retry()
        self.assertEqual(self.queued('a'), ['x.pdf'])
        self.assertEqual(self.queued('b'), [])
        self.assertEqual(b.stored, {'x.pdf': b'1'})

    def test_only_targeted_destinations_receive_the_file(self):
        a, b = FakeDestination('a'), FakeDestination('b')
        self.delivery(a=a, b=b).deliver(['b'], 'x.pdf', b'1')
        self.assertEqual((a.attempts, b.attempts), ([], ['x.pdf']))

    def test_same_filename_queued_twice_gets_unique_names(self):
        delivery = self.delivery(a=FakeDestination('a', failing=True))
        delivery.deliver(['a'], 'x.pdf', b'1')
        delivery.deliver(['a'], 'x.pdf', b'2')
        self.assertEqual(self.queued('a'), ['x (1).pdf', 'x.pdf'])

    def test_no_part_file_left_in_queue(self):
        delivery = self.delivery(a=FakeDestination('a', failing=True))
        delivery.deliver(['a'], 'x.pdf', b'1')
        self.assertFalse([n for n in self.queued('a') if n.endswith('.part')])

    def test_retry_loop_stops_when_event_is_set(self):
        stop = threading.Event()
        stop.set()
        dest = FakeDestination('a')
        self.delivery(a=dest).retry_loop(0, stop)  # returns immediately
        self.assertEqual(dest.attempts, [])


if __name__ == '__main__':
    unittest.main()
