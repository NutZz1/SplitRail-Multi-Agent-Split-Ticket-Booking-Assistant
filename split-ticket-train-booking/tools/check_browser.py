"""Optional browser acceptance check: pip install playwright; uses installed Chrome.

Starts an isolated server/booking ledger, exercises the actual UI, and always
terminates its server. Screenshots go to the supplied output directory.
"""
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright


def main():
    root = Path(__file__).resolve().parents[1]
    output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/splitrail-browser')
    output.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = str(sock.getsockname()[1])
    base_url = 'http://127.0.0.1:' + port
    with tempfile.TemporaryDirectory(prefix='splitrail-browser-') as temp:
        env = dict(os.environ, SPLIT_TICKET_BOOKINGS_DB=str(Path(temp)/'bookings.db'), SPLITRAIL_INVENTORY_PROFILE='busy-v2', SPLITRAIL_REGION='Kerala', SPLITRAIL_NETWORK_PROFILE='kerala-v1')
        with (output/'server.log').open('w') as log:
            server = subprocess.Popen([sys.executable, 'web.py', '--offline', '--port', port], cwd=root,
                                      env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(base_url + '/api/meta', timeout=1) as response:
                            meta = json.load(response)
                        break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError('Server failed: '+(output/'server.log').read_text())
                        time.sleep(.2)
                else:
                    raise RuntimeError('Server startup timed out')
                assert meta['station_count'] == 177
                assert meta['covered_station_count'] == 167
                assert meta['trains'] == 265
                assert 'SBC' not in {s['code'] for s in meta['stations']}
                with sync_playwright() as p:
                    browser = p.chromium.launch(channel='chrome', headless=True)
                    page = browser.new_page(viewport={'width':1440,'height':1000})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(base_url)
                    page.wait_for_selector('.journey-card', timeout=60000)
                    page.locator('#date').fill(meta['dates'][0])
                    page.locator('#max-transfers').select_option('1')
                    page.locator('#travel-class').select_option('SL')
                    with page.expect_response(lambda r: r.url.endswith('/api/search')) as searching:
                        page.locator('#search-button').click()
                    payload = searching.value.json()
                    assert payload['options']
                    page.wait_for_function("!document.getElementById('search-button').disabled")
                    assert page.locator('#direct-trains').count() == 0
                    groups = page.locator('.train-group').evaluate_all("nodes => nodes.map(n => n.dataset.train+':'+n.dataset.run)")
                    assert len(groups) == len(set(groups))
                    full = page.locator('.train-group[data-split-only="true"]').first
                    full.wait_for()
                    assert 'No through seat' in full.inner_text()
                    assert full.locator('.reserve-button:not([disabled])').count() > 0
                    full.evaluate("node => node.scrollIntoView({block:'start'})")
                    page.screenshot(path=str(output/'desktop.png'), full_page=False)
                    page.locator('.reserve-button:not([disabled])').first.click()
                    page.locator('#passenger-name').fill('Browser Demo')
                    with page.expect_response(lambda r: r.url.endswith('/api/book-itinerary')) as booking:
                        page.locator('#confirm-booking').click()
                    result = booking.value.json()
                    assert result['reference'].startswith('SIM-')
                    assert all(b['seat_numbers'] for b in result['bookings'])
                    page.wait_for_selector('#booking-confirmation h3')
                    page.screenshot(path=str(output/'confirmation.png'))
                    page.locator('#booking-dialog .dialog-close').click()
                    page.wait_for_selector('.cancel-button')
                    page.reload()
                    page.wait_for_selector('.cancel-button')
                    page.wait_for_function("!document.getElementById('search-button').disabled")
                    with page.expect_response(lambda r: '/api/bookings/' in r.url and r.request.method=='DELETE') as cancelling:
                        page.locator('.cancel-button').first.click()
                    assert cancelling.value.status == 200
                    page.wait_for_function("document.getElementById('bookings-list').textContent.includes('CANCELLED')")
                    with urllib.request.urlopen(base_url + '/api/bookings?status=', timeout=5) as response:
                        assert json.load(response)['bookings'][0]['status'] == 'CANCELLED'
                    page.wait_for_function("!document.getElementById('search-button').disabled")
                    page.wait_for_function("document.getElementById('status').textContent.includes('Reservation cancelled')")
                    page.locator('#filter-direct').uncheck()
                    page.locator('#filter-train').uncheck()
                    page.locator('.split-plan-button').first.click()
                    page.wait_for_selector('#split-dialog[open]')
                    assert 'Change Seat In Same Train At' in page.locator('#split-dialog-content').inner_text()
                    page.screenshot(path=str(output/'split-plan.png'))
                    page.locator('.split-book-btn').first.click()
                    page.locator('#passenger-name').fill('Browser Split Demo')
                    with page.expect_response(lambda r: r.url.endswith('/api/book-itinerary')) as split_booking:
                        page.locator('#confirm-booking').click()
                    split_result = split_booking.value.json()
                    assert len(split_result['bookings']) == 2
                    assert all(b['seat_numbers'] for b in split_result['bookings'])
                    page.wait_for_function("document.getElementById('status').textContent.includes('Booking saved')")
                    page.locator('#booking-dialog .dialog-close').click()
                    page.set_viewport_size({'width':390,'height':844})
                    page.evaluate('window.scrollTo(0, 0)')
                    page.screenshot(path=str(output/'mobile.png'))
                    page.locator('.journey-card').first.scroll_into_view_if_needed()
                    page.screenshot(path=str(output/'mobile-results.png'))
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
                    assert errors == [], errors
                    browser.close()
                    print(json.dumps({'browser':'Chrome', 'search_options':len(payload['options']),
                                      'booking':result['reference'], 'persistence':'passed',
                                      'same_train_split_booking':'passed',
                                      'cancellation':'passed', 'mobile_overflow':False,'js_errors':errors}))
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()


if __name__ == '__main__':
    main()
