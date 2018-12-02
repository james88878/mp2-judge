import os
import asyncio
import subprocess
from enum import Enum
import tempfile
import hashlib
import datetime

from peer_driver import PeerDriver
from log_tracker import LogTracker


class Status(Enum):
    OK = 'ok'
    ERROR = 'error'


class Comment(Enum):
    GIT_RESET_ERROR = 'Cannot reset repository'
    BUILD_FAILED = 'Build failed'
    EARLY_BONUS_NOT_REQUESTED = 'Early bonus not requested'
    EXCEPTION_RAISED = 'Exception raised'
    NOT_SUBMITTED = 'Homework is not submitted'
    PASS = 'Pass'
    TEST_FAILED = 'Test failed'


class Mp2Test:
    def __init__(self, student_id, github_account, repo_dir, logger=None, tmp_dir=None, auto_clean_tmp=True):
        self.student_id = student_id
        self.github_account = github_account
        self.repo_dir = repo_dir
        self.tmp_dir = tmp_dir
        self.auto_clean_tmp = auto_clean_tmp
        self.homework_dir = os.path.join(repo_dir, 'MP2')
        self.executable_path = os.path.join(self.homework_dir, 'loser_peer')
        self.logger = logger.getChild('Mp2Test')
        self.score = None
        self.is_clean_repo = False

        os.chdir(repo_dir)

    def git_clean_and_checkout(self, date):
        os.chdir(self.repo_dir)

        if date is not None:
            result = subprocess.run(
                "git reset --hard && git clean -dxf && git checkout $(git log --pretty='format:%H' --before={} -- . | head -n1)".format(date.strftime('%Y-%M-%dT%H:%M')),
                shell=True,
                capture_output=True
            )
        else:
            result = subprocess.run(
                "git reset --hard && git clean -dxf && git checkout master",
                shell=True,
                capture_output=True
            )

        if result.returncode != 0:
            self.is_clean_repo = False
            return False, Comment.GIT_RESET_ERROR, result

        self.is_clean_repo = True

        try:
            os.chdir(self.homework_dir)
        except FileNotFoundError as err:
            return False, Comment.NOT_SUBMITTED, err

        return True, None, None

    def build(self):
        assert self.is_clean_repo, 'Call git_reset_and_checkout() before build()'

        result = subprocess.run("make -B && [ -f loser_peer ]", shell=True, capture_output=True)
        return result.returncode == 0, result

    def early_bonus_test(self, due_date=datetime.datetime(2018, 11, 21, 23, 59), require_student_request=True):
        logger = self.logger.getChild('early_bonus_test')

        if self.auto_clean_tmp:
            test_dir = tempfile.TemporaryDirectory(dir=self.tmp_dir)
            test_dir_path = test_dir.name
        else:
            test_dir_path = tempfile.mkdtemp(dir=self.tmp_dir)

        log_tracker = LogTracker()

        try:
            logger.debug('Start early bonus test')

            # clean and checkout
            ok, comment, error = self.git_clean_and_checkout(due_date)

            if not ok:
                return Status.ERROR, comment, error

            assert self.is_clean_repo

            # check if early bonus judge is requested
            if require_student_request:
                request_file = os.path.join(self.homework_dir, 'early.md')

                if not os.path.isfile(request_file):
                    logger.info('Early bonus test is not requested, terminate test')
                    return Status.OK, Comment.EARLY_BONUS_NOT_REQUESTED, None

                logger.info('Early bonus test is requested')

            # build
            ok, result = self.build()

            if not ok:
                return Status.ERROR, Comment.BUILD_FAILED, result

            # prepare testing files
            resol_source_path = os.path.join(test_dir_path, 'resoL_source.txt')
            with open(resol_source_path, 'wb') as source_file:
                source_content = os.urandom(4096)

                digester = hashlib.md5()
                digester.update(source_content)
                resol_source_hash = bytes(digester.digest().hex(), 'ASCII')
                source_file.write(source_content)

            logger.debug('Generate test file %s with MD5 %s', resol_source_path, resol_source_hash)

            reep_source_path = os.path.join(test_dir_path, 'reep_source.txt')
            with open(reep_source_path, 'wb') as source_file:
                source_content = os.urandom(4096)

                digester = hashlib.md5()
                digester.update(source_content)
                reep_source_hash = bytes(digester.digest().hex(), 'ASCII')
                source_file.write(source_content)

            logger.debug('Generate test file %s with MD5 %s', reep_source_path, reep_source_hash)

            # start test
            async def test():
                resol_name = '%s-resol' % self.student_id
                reep_name = '%s-reep' % self.student_id

                peer_resol = PeerDriver(self.student_id, self.executable_path, resol_name, [reep_name], logger=logger, tmp_dir=self.tmp_dir, auto_clean_tmp=self.auto_clean_tmp)
                peer_reep = PeerDriver(self.student_id, self.executable_path, reep_name, [resol_name], logger=logger, tmp_dir=self.tmp_dir, auto_clean_tmp=self.auto_clean_tmp)

                try:
                    # start process
                    await peer_resol.start()
                    await peer_reep.start()
                    await asyncio.sleep(2.0)

                    # create first file
                    result, err = await peer_resol.send_cp(resol_source_path, '@resol.txt')
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    # take a nap
                    await asyncio.sleep(2.0)

                    # verify resol's history output
                    result, err = await peer_resol.send_histoy(False)
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    result, err = log_tracker.update_history('resol', result)
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    logger.debug('Parsed history output from resol: %s', result)
                    logger.info("Resol's history command output is correct")

                    # verify reep's history output
                    result, err = await peer_reep.send_histoy(False)
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    result, err = log_tracker.update_history('reep', result)
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    logger.debug('Parsed history output from reep: %s', result)
                    logger.info("Reep's history command output is correct")

                    # compare resol's and reep's history -a output
                    (result_resol, err_resol), (result_reep, err_reep) = await asyncio.gather(
                        peer_resol.send_histoy(True),
                        peer_reep.send_histoy(True)
                    )

                    if err_resol is not None:
                        return Status.OK, Comment.TEST_FAILED, err_resol
                    else:
                        logger.debug('Parsed history -a output from resol: %s', result_resol)

                    if err_reep is not None:
                        return Status.OK, Comment.TEST_FAILED, err_reep
                    else:
                        logger.debug('Parsed history -a output from reep: %s', result_reep)

                    if result_resol != result_reep:
                        logger.info('history -a command outputs differ among peers')
                        return Status.OK, Comment.TEST_FAILED, ('history -a command outputs differ among peers', result_resol, result_reep)

                    if not log_tracker.verify_combined_history(result_resol):
                        logger.info('Logs are combined incorrectly')
                        return Status.OK, Comment.TEST_FAILED, (
                            'Logs are combined incorrectly, result={}, expected={}'.format(
                                result_resol,
                                log_tracker.get_expected_combined_history(),
                            )
                        )

                    logger.info('history -a command output is correct')

                    # verify if logical view is calculate correctly
                    answer_view = {b'resol.txt': resol_source_hash}
                    current_view = log_tracker.get_expected_logical_view()

                    logger.debug('Logical view computed from combined history: {}'.format(current_view))

                    if current_view != answer_view:
                        logger.info('Your combined history does not work correctly')
                        return Status.OK, Comment.TEST_FAILED, 'Expect logical view {} by replaying combined history, but get {}'.format(answer_view, current_view)

                    # verify resol's list output
                    result, err = await peer_resol.send_list()
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    logger.debug('Parsed list output from resol: %s', result)

                    if result != answer_view:
                        return Status.OK, Comment.TEST_FAILED, 'Wrong list command output: {}, expect: {}'.format(result, answer_view)

                    logger.info("Resol's list command output is correct")

                    # verify reep's list output
                    result, err = await peer_reep.send_list()
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    logger.debug('Parsed list output from reep: %s', result)

                    if result != answer_view:
                        return Status.OK, Comment.TEST_FAILED, 'Wrong list command output: {}, expect: {}'.format(result, answer_view)

                    logger.info("Reep's list command output is correct")

                    # create second file
                    result, err = await peer_resol.send_cp(reep_source_path, '@reep.txt')
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    # take a nap
                    await asyncio.sleep(2.0)

                    # verify resol's history output
                    result, err = await peer_resol.send_histoy(False)
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    result, err = log_tracker.update_history('resol', result)
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    logger.debug('Parsed history output from resol: %s', result)
                    logger.info('history command output is correct from resol')

                    # verify reep's history output
                    result, err = await peer_reep.send_histoy(False)
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    result, err = log_tracker.update_history('reep', result)
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    logger.debug('Parsed history output from reep: %s', result)
                    logger.info('history command output is correct from reep')

                    # compare resol's and reep's history -a output
                    (result_resol, err_resol), (result_reep, err_reep) = await asyncio.gather(
                        peer_resol.send_histoy(True),
                        peer_reep.send_histoy(True)
                    )

                    if err_resol is not None:
                        return Status.OK, Comment.TEST_FAILED, err_resol
                    else:
                        logger.debug('Parsed history -a output from resol: %s', result_resol)

                    if err_reep is not None:
                        return Status.OK, Comment.TEST_FAILED, err_reep
                    else:
                        logger.debug('Parsed history -a output from reep: %s', result_reep)

                    if result_resol != result_reep:
                        logger.info('history -a command outputs differ among peers')
                        return Status.OK, Comment.TEST_FAILED, ('history -a command outputs differ among peers', result_resol, result_reep)

                    if not log_tracker.verify_combined_history(result_resol):
                        logger.info('Logs are combined incorrectly')
                        return Status.OK, Comment.TEST_FAILED, (
                            'Logs are combined incorrectly, result={}, expected={}'.format(
                                result_resol,
                                log_tracker.get_expected_combined_history(),
                            )
                        )

                    logger.info('history -a command output is correct')

                    # verify if logical view is calculate correctly
                    answer_view = {
                        b'resol.txt': resol_source_hash,
                        b'reep.txt': reep_source_hash,
                    }
                    current_view = log_tracker.get_expected_logical_view()

                    logger.debug('Logical view computed from combined history: {}'.format(current_view))

                    if current_view != answer_view:
                        logger.info('Your combined history does not work correctly')
                        return Status.OK, Comment.TEST_FAILED, 'Expect logical view {} by replaying combined history, but get {}'.format(answer_view, current_view)

                    # verify resol list command output

                    result, err = await peer_resol.send_list()
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    if result != answer_view:
                        return Status.OK, Comment.TEST_FAILED, 'Wrong list command output: {}, expect: {}'.format(result, answer_view)

                    logger.info('list command output is correct from resol')

                    # verify reep list command output
                    result, err = await peer_reep.send_list()
                    if err is not None:
                        return Status.OK, Comment.TEST_FAILED, err

                    if result != answer_view:
                        return Status.OK, Comment.TEST_FAILED, 'Wrong list command output: {}, expect: {}'.format(result, answer_view)

                    logger.info('list command output is correct from reep')

                    # send exit
                    logger.info('"exit" command is not judged in early bonus test')
                    logger.info('The message is only informative and does not affect scores')

                    (result_resol, err_resol), (result_reep, err_reep) = await asyncio.gather(
                        peer_resol.send_exit(),
                        peer_reep.send_exit(),
                    )

                    if err_resol is not None:
                        logger.info('exit command failed: %s', err_resol)
                    elif not result_resol:
                        logger.info('exit command failed: %s', err_resol)

                    if err_reep is not None:
                        logger.info('exit command failed: %s', err_reep)
                    elif not result_reep:
                        logger.info('exit command failed: %s', err_reep)

                    return Status.OK, Comment.PASS, None

                finally:
                    await asyncio.gather(
                        peer_resol.kill(),
                        peer_reep.kill(),
                    )

            return asyncio.run(test())

        finally:
            if self.auto_clean_tmp:
                test_dir.cleanup()
