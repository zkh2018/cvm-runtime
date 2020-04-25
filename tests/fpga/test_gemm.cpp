#include <CL/opencl.h>
#include <iostream>
#include <string>
#include <vector>
#include <iostream>
#include <fstream>
#include "util.hpp"
using namespace std;

void gemm_cpu(const char * A, const char *B, const int *bias, int* C,
	const int M, const int K, const int N){
  for(int i = 0; i < M; i++){
    for(int j = 0; j < N; j++){
      int sum = 0;
      for(int k = 0; k < K; k++){
        sum += A[i*K + k] * B[k * N + j]; 
      }
      C[i*N+j] = sum + bias[i];
    }
  }
}

void gemm_fpga(const char *A, const char *B, const int *bias, int *C, const int M, const int K, const int N){
  cl_int code;
  const int TM = (M+63)/64*64;
  const int TK = (K+63)/64*64;
  const int TN = (N+63)/64*64;
  int size = TM*TK + TK*TN;

  cl_mem buf_space = clCreateBuffer(context, CL_MEM_READ_WRITE, sizeof(char)*size, NULL, &code);
  //cl_mem bufA = clCreateBuffer(context, CL_MEM_READ_WRITE, sizeof(int)*M*K, NULL, &code);
  //cl_mem bufB = clCreateBuffer(context, CL_MEM_READ_WRITE, sizeof(char)*K*N, NULL, &code);
  cl_mem bufb = clCreateBuffer(context, CL_MEM_READ_WRITE, sizeof(int)*M, NULL, &code);

  clEnqueueWriteBuffer(queue, buf_space, CL_TRUE, 0, sizeof(char)*M*K, A, 0, nullptr, nullptr);
  clEnqueueWriteBuffer(queue, buf_space, CL_TRUE, sizeof(char)*TM*TK, sizeof(char)*N*K, B, 0, nullptr, nullptr);
  clEnqueueWriteBuffer(queue, bufb, CL_TRUE, 0, sizeof(int)*M, bias, 0, nullptr, nullptr);

  cl_mem bufC = clCreateBuffer(context, CL_MEM_READ_WRITE, sizeof(int)*M*N, NULL, &code);
  cl_kernel kernel = bias != NULL ? clCreateKernel(program, "gemm_bias", &code) : clCreateKernel(program, "gemm", &code);
  int index = 0;
  clSetKernelArg(kernel, index++, sizeof(cl_mem), (void*)&buf_space);
  clSetKernelArg(kernel, index++, sizeof(cl_mem), (void*)&buf_space);
  if(bias != NULL)
    clSetKernelArg(kernel, index++, sizeof(cl_mem), (void*)&bufb);
  clSetKernelArg(kernel, index++, sizeof(cl_mem), (void*)&bufC);
  clSetKernelArg(kernel, index++, sizeof(int), (void*)&M);
  clSetKernelArg(kernel, index++, sizeof(int), (void*)&K);
  clSetKernelArg(kernel, index++, sizeof(int), (void*)&N);
  clEnqueueTask(queue, kernel, 0, NULL, NULL);

  clEnqueueReadBuffer(queue, bufC, CL_TRUE, 0, sizeof(int)*M*N, C, 0, nullptr, nullptr); 

  //release
}

int main(){
  init_opencl("ops.xclbin");

  const int M = 16;
  const int K = 16;
  const int N = 16;
  char *A = new char[M*K];
  char *B = new char[K*N];
  int *C = new int[M*N];
  int *C2 = new int[M*N];
  int *bias = new int[M];

  for(int i = 0; i < M*K; i++){
    A[i] = i % 127;
  }
  for(int i = 0; i < N*K; i++){
    B[i] = i % 127;
  }
  for(int i = 0; i < M; i++){
    bias[i] = i;
  }

  gemm_cpu(A, B, bias, C, M, K, N);
  gemm_fpga(A, B, bias, C2, M, K, N);

  verify(C, C2, M*N);
}
