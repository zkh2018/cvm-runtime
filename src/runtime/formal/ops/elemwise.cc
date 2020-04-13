#include "ops.h"

namespace cvm {
namespace runtime {
  

CVM_REGISTER_GLOBAL("cvm.runtime.formal.elemwise_add")
    .set_body([](CVMArgs args, CVMRetValue *ret)
{
  DLTensor *args0 = args[0];
  DLTensor *args1 = args[1];
  DLTensor *args2 = args[2];
  
  int32_t *a = static_cast<int32_t*>(args0->data);
  int32_t *b = static_cast<int32_t*>(args1->data);
  int32_t *c = static_cast<int32_t*>(args2->data);

  for(uint64_t i = 0; i < getSize(args0); i++){
    c[i] = a[i] + b[i];
  }
  print_to_file(args2, "elemwise_add.txt");
});

CVM_REGISTER_GLOBAL("cvm.runtime.formal.elemwise_sub")
    .set_body([](CVMArgs args, CVMRetValue *ret)
{
  DLTensor *args0 = args[0];
  DLTensor *args1 = args[1];
  DLTensor *args2 = args[2];

  int32_t *a = static_cast<int32_t*>(args0->data);
  int32_t *b = static_cast<int32_t*>(args1->data);
  int32_t *c = static_cast<int32_t*>(args2->data);

  for(uint64_t i = 0; i < getSize(args0); i++){
    c[i] = a[i] - b[i];
  }
});

CVM_REGISTER_GLOBAL("cvm.runtime.formal.clip")
.set_body([](CVMArgs args, CVMRetValue* rv){
   DLTensor *x = args[0];
   DLTensor *y = args[1];
   void *_attr = args[2];
   auto *attr = static_cast<cvm::NodeAttrs*>(_attr);
   auto& param = cvm::get<cvm::top::ClipParam>(attr->parsed);
   int32_t a_max = param.a_max;
   int32_t a_min = param.a_min;
   int32_t *x_data = static_cast<int32_t*>(x->data);
   int32_t *y_data = static_cast<int32_t*>(y->data);
   for (uint64_t i = 0; i < getSize(x); i++) {
      if (x_data[i] >= a_max){
        y_data[i] = a_max;
      } else if (x_data[i] <= a_min) {
        y_data[i] = a_min;
      } else {
        y_data[i] = x_data[i];
      }
    }
});

CVM_REGISTER_GLOBAL("cvm.runtime.formal.flatten")
    .set_body([](CVMArgs args, CVMRetValue* rv)
{
     DLTensor *x = args[0];
     DLTensor *y = args[1];
     int32_t* x_data = static_cast<int32_t*>(x->data);
     int32_t* y_data = static_cast<int32_t*>(y->data);
     if(x_data != y_data){
        memcpy(y_data, x_data, getSize(x)*sizeof(int32_t));
     }


  print_to_file(y, "flatten.txt");
});

CVM_REGISTER_GLOBAL("cvm.runtime.formal.reshape")
    .set_body([](CVMArgs args, CVMRetValue *ret)
{
  DLTensor *x = args[0];
  DLTensor *y = args[1];
  if(x->data == y->data) return;
  std::memcpy(y->data, x->data, getSize(x) * sizeof(int32_t));
  print_to_file(y, "reshape.txt");
});

CVM_REGISTER_GLOBAL("cvm.runtime.formal.cvm_clip")
    .set_body([](CVMArgs args, CVMRetValue *ret)
{
  DLTensor *x = args[0];
  DLTensor *y = args[1];
  int32_t *x_data = static_cast<int32_t*>(x->data);
  int32_t *y_data = static_cast<int32_t*>(y->data);

  void *_attr = args[2];
  auto *attr = static_cast<cvm::NodeAttrs*>(_attr);
  auto &param = cvm::get<cvm::top::CVMClipParam>(attr->parsed);
  int32_t precision = param.precision;
  int32_t a_min = -(((int64_t)1 << (precision-1))-1);
  int32_t a_max = -a_min;

  for(uint64_t i = 0; i < getSize(x); i++){
      if (x_data[i] >= a_max){
        y_data[i] = a_max;
      } else if (x_data[i] <= a_min) {
        y_data[i] = a_min;
      } else {
        y_data[i] = x_data[i];
      }
  }
  print_to_file(y, "clip.txt");
}
);

CVM_REGISTER_GLOBAL("cvm.runtime.formal.cvm_right_shift")
.set_body([](CVMArgs args, CVMRetValue *ret){
    auto X = CVMArg2Data<int32_t>(args[0]);
    auto Y = CVMArg2Data<int32_t>(args[1]);
    auto params = CVMArg2Attr<top::CVMRightShiftParam>(args[2]);

    int64_t max_size = CVMArgSize(args[0]);

    int32_t alpha = (1 << (params.precision - 1)) - 1;
    for (uint64_t i = 0; i < max_size; ++i) {
      int t = X[i] >> (params.shift_bit - 1);
      t = (t + 1) >> 1;
      Y[i] = std::min(std::max(t, -alpha), alpha);
    }

});

CVM_REGISTER_GLOBAL("cvm.runtime.formal.cvm_left_shift")
.set_body([](CVMArgs args, CVMRetValue *ret){
    DLTensor *a = args[0];
    DLTensor *c = args[1];
    void *_attr = args[2];
    auto *attr = static_cast<cvm::NodeAttrs*>(_attr);
    auto &param = cvm::get<cvm::top::CVMLeftShiftParam>(attr->parsed);
    int32_t precision = param.precision;
    int32_t b = param.shift_bit;std::string str_precision = args[2];
    int32_t* a_data = static_cast<int32_t*>(a->data);
    int32_t* c_data = static_cast<int32_t*>(c->data);
    int32_t min = -(((int64_t)1 << (precision-1)) - 1);
    int32_t max = -min;

    for(uint64_t i = 0; i < getSize(a); i++){
      int32_t shift_a = a_data[i] << b;
      c_data[i] = std::max(std::min(shift_a, max), min);
    }
});
}
}
