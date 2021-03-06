from ..farnsworth_api_wrapper import CRSAPIWrapper
from farnsworth.models import Exploit
from common_utils.binary_tester import BinaryTester
import collections
from multiprocessing.dummy import Pool as ThreadPool
from common_utils.simple_logging import log_info, log_success, log_failure, log_error
import os
import compilerex
NUM_THROWS = 10


def _test_pov(thread_arg):
    """
        Test the provided PoV.
    :param thread_arg: (bin_folder, pov_file, ids_rules) tuple.
    :return: True if successful else False.
    """
    bin_folder = thread_arg[0]
    pov_file = thread_arg[1]
    ids_rules = thread_arg[2]
    bin_tester = BinaryTester(bin_folder, pov_file, is_pov=True, is_cfe=True, standalone=True, ids_rules=ids_rules)
    ret_code, stdout_txt, stderr_txt = bin_tester.test_cb_binary()
    return ret_code == 0, stdout_txt, stderr_txt


def _get_all_cbns(cs_fielded_obj):
    """
        Get all cbns of the provided fielded cs
    :param cs_fielded_obj: fielded cs for which we need to get the CBns for.
    :return: list of cbs of the provided fielded cs.
    """
    return cs_fielded_obj.cbns


def _get_ids_rules_obj(ids_fielding_obj):
    """
        Get the ids rule object of the provided ids fielding obj.
    :param ids_fielding_obj: fielded IDS for which we need to get rules for.
    :return: ids_rules obj of the provided ids_fielding_obj
    """
    if ids_fielding_obj is not None:
        return ids_fielding_obj.ids_rule
    return None


def _get_job_args(curr_pov_test_job):
    """
        Get arguments for a provided test job.
    :param curr_pov_test_job: pov test job for which arguments needs to be fetched.
    :return: (bin_dir, work_dir, pov_file_path) tuple
    """
    cs_fielding_obj = curr_pov_test_job.target_cs_fielding
    pov_test_job_id = curr_pov_test_job.id
    # Get all binaries in
    all_cbns = _get_all_cbns(cs_fielding_obj)
    curr_work_dir = os.path.join(os.path.expanduser("~"), "pov_tester_" + str(pov_test_job_id))

    bin_dir = os.path.join(curr_work_dir, 'bin_dir')
    pov_dir = os.path.join(curr_work_dir, 'pov_dir')
    ids_dir = os.path.join(curr_work_dir, 'ids_rules')

    try:

        # set up binaries
        # Save CBNs into the bin dir
        os.system('mkdir -p ' + str(bin_dir))
        for curr_cb in all_cbns:
            curr_file = str(curr_cb.cs_id) + '_' + str(curr_cb.name)
            curr_file_path = os.path.join(bin_dir, curr_file)
            fp = open(curr_file_path, 'wb')
            fp.write(curr_cb.blob)
            fp.close()
            os.chmod(curr_file_path, 0o777)

        pov_file_path = None

        # set up povs
        # save povs into pov directory
        os.system('mkdir -p ' + str(pov_dir))
        target_exploit_obj = curr_pov_test_job.target_exploit
        pov_file_path = os.path.join(pov_dir, str(curr_pov_test_job.id) + '.pov')
        fp = open(pov_file_path, 'w')
        fp.write(str(target_exploit_obj.blob))
        fp.close()
        os.chmod(pov_file_path, 0o777)

        ids_file_path = None
        # set up ids rules
        # save ids rules into directory
        os.system('mkdir -p ' + str(ids_dir))
        ids_rules_obj = _get_ids_rules_obj(curr_pov_test_job.target_ids_fielding)
        # if we have non-empty ids rules?
        if ids_rules_obj is not None and ids_rules_obj.rules is not None and len(str(ids_rules_obj.rules).strip()) > 0:
            ids_file_path = os.path.join(ids_dir, str(curr_pov_test_job.id) + '_ids.rules')
            fp = open(ids_file_path, 'w')
            fp.write(str(ids_rules_obj.rules))
            fp.close()
            os.chmod(ids_file_path, 0o777)
    except Exception as e:
        # clean up
        if curr_work_dir is not None:
            os.system('rm -rf ' + curr_work_dir)
        log_error("Error occurred while trying to setup working directory for PovTesterJob:" + str(pov_test_job_id) +
                  ", Error:" + str(e))
        raise e
    return bin_dir, curr_work_dir, pov_file_path, ids_file_path


def is_testing_not_required(pov_test_job):
    """
        Is testing required for the provided Pov Tester Job.
    :param pov_test_job: Pov Tester job which needs to be checked.
    :return: True/False: indicating whether testing is not required.
    """
    SUCCESS_THRESHOLD = 4
    try:
        curr_result = CRSAPIWrapper.get_best_pov_result(pov_test_job.target_cs_fielding,
                                                        pov_test_job.target_ids_fielding)
        return curr_result is not None and curr_result.num_success >= SUCCESS_THRESHOLD
    except Exception as e:
        log_error("Error occured while trying to get available results for pov tester job:" + str(pov_test_job.id) +
                  ", Error:" + str(e))
    return False


def process_povtester_job(curr_job_args):
    """
        Process the provided PoV Tester Job with given number of threads.
    :param curr_job_args: (pov tester job to process, number of threads that could be used)
    :return: None
    """
    CRSAPIWrapper.open_connection()
    job_id = curr_job_args[0]
    curr_job = CRSAPIWrapper.get_pov_tester_job(job_id)
    num_threads = curr_job_args[1]
    target_job = curr_job
    job_id_str = str(curr_job.id)

    if target_job.try_start():
        if is_testing_not_required(curr_job):
            log_success("Testing not required for PovTesterJob:" + str(job_id) + ", as a previous job obviated this.")
        else:
            curr_work_dir = None
            try:
                job_bin_dir, curr_work_dir, pov_file_path, ids_rules_path = _get_job_args(curr_job)
                job_id_str = str(curr_job.id)
                log_info("Trying to run PovTesterJob:" + job_id_str)
                all_child_process_args = []

                for i in range(NUM_THROWS):
                    all_child_process_args.append((job_bin_dir, pov_file_path, ids_rules_path))

                log_info("Got:" + str(len(all_child_process_args)) + " Throws to test for PovTesterJob:" + job_id_str)

                all_results = []
                # If we can multiprocess? Run in multi-threaded mode
                if num_threads > 1:
                    log_info("Running in multi-threaded mode with:" + str(num_threads) + " threads. For PovTesterJob:" +
                             job_id_str)
                    thread_pool = ThreadPool(processes=num_threads)
                    all_results = thread_pool.map(_test_pov, all_child_process_args)
                    thread_pool.close()
                    thread_pool.join()
                else:
                    log_info("Running in single threaded mode. For PovTesterJob:" +
                             job_id_str)
                    for curr_child_arg in all_child_process_args:
                        all_results.append(_test_pov(curr_child_arg))

                throws_passed = len(filter(lambda x: x[0], all_results))
                # if none of the throws passed, lets see if we can create new exploit?
                if throws_passed == 0:
                    log_info("Exploit is bad. Trying to create new exploit by replacing most common register.")
                    all_regs = collections.defaultdict(int)
                    for _,curr_output,_ in all_results:
                        curr_exploit_reg = _get_exploit_register(curr_output)
                        if curr_exploit_reg is not None:
                            all_regs[curr_exploit_reg] += 1
                    if len(all_regs) > 0:
                        log_info("Got:" + str(len(all_regs)) + " possible registers")
                        target_reg = sorted(all_regs.items(), key=lambda x: x[1], reverse=True)[0][0]
                        log_success("Using:" + target_reg + " to create a new exploit")
                        _fixup_exploit(curr_job.target_exploit, target_reg)
                    else:
                        log_failure("Could not get any register from cb-test output")

                CRSAPIWrapper.create_pov_test_result(curr_job.target_exploit, curr_job.target_cs_fielding,
                                                     curr_job.target_ids_fielding, throws_passed)
                log_success("Done Processing PovTesterJob:" + job_id_str)
            except Exception as e:
                log_error("Error Occured while processing PovTesterJob:" + job_id_str + ". Error:" + str(e))
            # clean up
            if curr_work_dir is not None:
                os.system('rm -rf ' + curr_work_dir)
        target_job.completed()
    else:
        log_failure("Ignoring PovTesterJob:" + job_id_str + " as we failed to mark it busy.")
    CRSAPIWrapper.close_connection()


def _get_exploit_register(stdout_txt):
    """
        get exploit register from output of cb-test
    :param stdout_txt: output from cb-test
    :return: correct register name or none ( on error)
    """
    magic_token = 'incorrect reg - should set'
    exploit_reg = None
    try:
        if stdout_txt is not None:
            stdout_txt = str(stdout_txt).strip()
            all_lines = stdout_txt.split('\n')
            for curr_line in all_lines:
                if magic_token in curr_line:
                    exploit_reg = curr_line.split(magic_token)[1].strip()
                    break
    except Exception as e:
        log_error("Error occurred while trying to get correct register from cb-test output:" + str(e))
    return exploit_reg


def _fixup_exploit(exploit, register):
    """
    :param exploit: the peewee exploit object (should be type 1 only)
    :param register: the register we are actually setting
    :return: the new exploit object or None if we shouldn't create one
    """
    # make compilerex executable
    bin_path = os.path.join(os.path.dirname(compilerex.__file__), "../bin")
    for f in os.listdir(bin_path):
        os.chmod(os.path.join(bin_path, f), 0777)
        os.chmod(os.path.join(bin_path, f), 0777)

    c_code = str(exploit.c_code)
    if c_code.startswith("//FIXED"):
        return None

    fixed_lines = ["//FIXED"]
    for line in c_code.split("\n"):
        # fix the line which sets the regnum
        if "enum register_t regnum" in line:
            fixed_lines.append("  enum register_t regnum = %s;" % register)
        else:
            fixed_lines.append(line)
    new_c = "\n".join(fixed_lines)

    compiled_blob = compilerex.compile_from_string(new_c)

    # we keep the reliability at 0.0, however the tester needs to make sure to mark
    # this exploit good for this fielding
    e = Exploit.create(cs=exploit.cs, job=exploit.job, pov_type='type1',
                       method=exploit.method, c_code=new_c,
                       blob=compiled_blob, crash=exploit.crash)

    e.save()
    return e
