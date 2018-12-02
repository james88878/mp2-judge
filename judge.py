#!/usr/bin/env python3
import os
import csv
import argparse
import datetime
import multiprocessing as mp
import logging
import pathlib
import traceback


import coloredlogs
from mp2_test import Mp2Test, Status, Comment


def main():
    # compute default paths
    default_tmp_dir = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        'judge_tmp'
    )

    default_log_dir = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        'judge_log'
    )

    # parse arguments
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument('ACCOUNT_FILE')
    arg_parser.add_argument('REPO_DIR')
    arg_parser.add_argument('--log-dir', default=default_log_dir)
    arg_parser.add_argument('--log-level', default='INFO')
    arg_parser.add_argument('--tmp-dir', default=default_tmp_dir)
    arg_parser.add_argument('--keep-tmp-files', action='store_true')
    args = arg_parser.parse_args()

    # make sure temp dir exists
    if args.tmp_dir is not None:
        pathlib.Path(args.tmp_dir).mkdir(parents=True, exist_ok=True)

    # setup logging
    pathlib.Path(args.log_dir).mkdir(parents=True, exist_ok=True)
    log_file = os.path.join(os.path.realpath(args.log_dir), 'log.txt')
    logger = logging.getLogger('main')
    logger.setLevel(args.log_level)

    formatter = logging.Formatter('[%(levelname)s] (%(asctime)s) %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler = logging.FileHandler(log_file, mode='w', encoding='UTF-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    with open(args.ACCOUNT_FILE, 'r') as file_account:
        reader = csv.reader(file_account)
        account_list = list(map(lambda row: (row[0].upper(), row[1]), reader))

    # start process pool
    def make_judge_arg(row):
        student_id = row[0]
        github_account = row[1]
        repo_dir =  os.path.join(
            os.path.realpath(args.REPO_DIR),
            'SP18-{}'.format(row[0]),
        )
        log_dir = os.path.realpath(args.log_dir)
        return (student_id, github_account, repo_dir, log_dir, args.log_level, args.tmp_dir, not args.keep_tmp_files)

    judge_args = map(make_judge_arg, account_list)

    with mp.Pool() as pool:
        gen_results = pool.imap_unordered(judge, judge_args)

        for result in gen_results:
            student_id, context = result


def judge(args):
    student_id, github_account, repo_dir, log_dir, log_level, tmp_dir, auto_clean_tmp = args

    # setup logger
    log_file = os.path.join(log_dir, '{}.log'.format(student_id))
    logger = logging.getLogger(student_id)
    formatter = logging.Formatter('[%(levelname)s]\t(%(asctime)s)\t%(name)s:\t%(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler = logging.FileHandler(log_file, mode='w', encoding='UTF-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    coloredlogs.install(level=log_level)

    logger.debug('Start judging %s', student_id)

    context = {
        'status': None,
        'comment': None,
        'score': None,
        'error': None,
    }

    try:
        tester = Mp2Test(student_id, github_account, repo_dir, logger=logger, tmp_dir=tmp_dir, auto_clean_tmp=auto_clean_tmp)

        # early bonus test
        status, comment, error = tester.early_bonus_test(due_date=None)

        if status == Status.OK and comment == Comment.PASS:
            context['score'] = 3

        context['status'] = status
        context['comment'] = comment
        context['error'] = error

    except Exception as err:
        context['status'] = Status.ERROR
        context['comment'] = Comment.EXCEPTION_RAISED
        context['error'] = err
        traceback.print_exc()
    finally:
        # assersions
        assert context['status'] is not None
        assert context['comment'] is not None

        # write log
        logger.info('status=%s', context['status'].value)
        logger.info('comment=%s', context['comment'].value)
        logger.info('score=%s', context['score'])
        logger.info('error=%s', context['error'])

        return student_id, context


if __name__ == '__main__':
    main()
