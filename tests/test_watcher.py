import re
from datetime import datetime, timedelta
from src.config import PollConfig
from src import watcher as W

def test_exam_url():
    assert W.exam_url("87130") == "https://us.prairietest.com/pt/student/exam/87130"

def test_discover_exam_id():
    html = '''
    <a href="/pt/student/exam/999">CPSC 320 (2026W1): Midterm 1</a>
    <a href="/pt/student/exam/87130">CPSC 313 (2026W1): CPSC 313 Quiz 1</a>
    '''
    assert W.discover_exam_id(html, re.compile("CPSC 313 Quiz 1", re.I)) == "87130"

def test_discover_exam_id_none_when_absent():
    html = '<a href="/pt/student/exam/999">Other exam</a>'
    assert W.discover_exam_id(html, re.compile("CPSC 313 Quiz 1", re.I)) is None

def test_is_logged_out_by_url():
    assert W.is_logged_out("<html>anything</html>", "https://authentication.ubc.ca/login") is True

def test_is_logged_out_by_marker():
    assert W.is_logged_out("<h1>Sign in with your CWL</h1>", "https://us.prairietest.com/pt") is True

def test_is_logged_in():
    assert W.is_logged_out("<main>Exam reservation</main>", "https://us.prairietest.com/pt/student/exam/87130") is False

def test_is_logged_out_on_prairietest_landing_splash():
    # Logged-out PrairieTest shows a marketing splash + Login button, NOT a redirect.
    html = "<h1>PrairieTest</h1><p>An exam proctoring system</p><a>Login</a>"
    assert W.is_logged_out(html, "https://us.prairietest.com/pt") is True

def test_is_logged_in_home_not_logged_out():
    html = "<main>PrairieTest Homepage Exams available for reservations Exam reservations</main>"
    assert W.is_logged_out(html, "https://us.prairietest.com/pt") is False

def test_is_logged_out_on_auth_intermediates():
    assert W.is_logged_out("<html></html>", "https://us.prairielearn.com/pl/login") is True
    assert W.is_logged_out("<html></html>", "https://api-x.duosecurity.com/frame") is True

def test_next_interval_base():
    poll = PollConfig(interval_seconds=15, jitter_seconds=0, open_time=None, ramp_interval_seconds=2)
    assert W.next_interval(datetime(2026,10,1,8,0,0), poll) == 15

def test_next_interval_ramps_near_open_time():
    ot = datetime(2026,10,1,9,0,0)
    poll = PollConfig(interval_seconds=15, jitter_seconds=0, open_time=ot, ramp_interval_seconds=2)
    assert W.next_interval(ot - timedelta(seconds=30), poll) == 2   # within ramp window
    assert W.next_interval(ot - timedelta(seconds=300), poll) == 15 # outside window

def test_matching_exams_returns_all_matches():
    html = '''
    <a href="/pt/student/exam/1">CPSC 313 (2026W1): Quiz 1</a>
    <a href="/pt/student/exam/2">CPSC 313 (2026W1): Quiz 2</a>
    <a href="/pt/student/exam/3">CPSC 320 (2026W1): Midterm</a>
    <a href="/pt/student/exam/1">CPSC 313 (2026W1): Quiz 1</a>
    '''
    out = W.matching_exams(html, re.compile("CPSC 313", re.I))
    assert out == [("1", "CPSC 313 (2026W1): Quiz 1"), ("2", "CPSC 313 (2026W1): Quiz 2")]

def test_matching_exams_available_exam_name_in_aria_label():
    # Live "available for reservations" row: the <a> text is just the button
    # label; the exam name is in aria-label.
    html = '''
    <div class="card"><div class="row">
      <div class="col"><a class="btn btn-success" href="/pt/student/exam/87163"
          aria-label="Make a reservation for CPSC 320 (2026W1): Test 1">Make a reservation</a></div>
      <div class="col">CPSC 320 (2026W1): Test 1</div>
      <div class="col">Mon, Oct 5 to Fri, Oct 9 (PDT)</div>
    </div></div>
    '''
    out = W.matching_exams(html, re.compile("CPSC 320", re.I))
    assert len(out) == 1 and out[0][0] == "87163" and "CPSC 320" in out[0][1]

def test_matching_exams_available_exam_name_in_sibling_cell():
    # Same, but the name is only in a sibling cell (aria-labelledby style).
    html = '''
    <div class="list-group-item"><div class="row">
      <div class="col"><a href="/pt/student/exam/999">Make a reservation</a></div>
      <div class="col">MATH 200 (2026W1): Final</div>
    </div></div>
    '''
    out = W.matching_exams(html, re.compile("MATH 200", re.I))
    assert len(out) == 1 and out[0][0] == "999"

def test_matching_exams_scopes_to_row_not_whole_page():
    # Two separate available exams: matching one must not pull the other's id.
    html = '''
    <ul>
      <li class="list-group-item"><a href="/pt/student/exam/11" aria-label="Make a reservation for CPSC 320 Test 1">Make a reservation</a></li>
      <li class="list-group-item"><a href="/pt/student/exam/22" aria-label="Make a reservation for CPSC 313 Quiz 1">Make a reservation</a></li>
    </ul>
    '''
    out = W.matching_exams(html, re.compile("CPSC 320", re.I))
    assert out == [("11", "Make a reservation for CPSC 320 Test 1")]
