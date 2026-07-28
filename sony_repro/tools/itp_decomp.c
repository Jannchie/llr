// 8 functions, roots=0x35af00 0x35ad60 0x35b810 0x35ecc0 0x362df0 0x363500 0x360910 0x35d3e0 depth=0

// ===== depth0 FUN_14035af00 @ 0x14035af00 rva=0x35af00 size=2313 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */

int FUN_14035af00(longlong *param_1,longlong param_2,undefined8 *param_3)

{
  undefined4 uVar1;
  undefined4 uVar2;
  float fVar3;
  void *pvVar4;
  int iVar5;
  longlong lVar6;
  longlong lVar7;
  undefined8 *puVar8;
  undefined8 uVar9;
  float *pfVar10;
  undefined2 *puVar11;
  undefined8 *puVar12;
  ulonglong uVar13;
  longlong lVar14;
  longlong lVar15;
  uint uVar16;
  undefined8 *puVar17;
  undefined8 *puVar18;
  float fVar19;
  float fVar20;
  undefined1 auStack_188 [32];
  undefined8 **local_168;
  undefined8 *local_160;
  undefined8 *local_158;
  undefined ***local_150;
  undefined ***local_148;
  undefined8 *local_140;
  undefined *local_138;
  undefined **local_128;
  uint local_120;
  uint local_11c;
  uint local_118;
  uint local_114;
  uint local_110;
  undefined8 *local_108;
  uint local_100;
  longlong local_f8;
  undefined8 *local_f0;
  undefined8 *local_e8;
  undefined8 *local_e0;
  undefined8 *local_d8;
  undefined8 *local_d0;
  longlong local_c8;
  void *local_c0;
  undefined **local_b8;
  uint local_b0;
  uint local_ac;
  uint local_a8;
  uint local_a4;
  undefined1 local_a0 [40];
  undefined1 local_78 [40];
  undefined8 *local_50;
  ulonglong local_48;
  
  local_48 = DAT_140559440 ^ (ulonglong)auStack_188;
  local_c0 = (void *)0x0;
  if ((param_2 == 0) || (*(longlong *)(param_2 + 8) == 0)) {
    iVar5 = 1;
  }
  else {
    local_f8 = param_2;
    lVar6 = FUN_140152710(*(longlong *)(param_2 + 8),0);
    uVar1 = *(undefined4 *)(lVar6 + 8);
    uVar2 = *(undefined4 *)(lVar6 + 0xc);
    lVar7 = (**(code **)(*(longlong *)*param_3 + 0xd8))
                      ((longlong *)*param_3,*(undefined4 *)(param_3[1] + 0x1e0));
    if (lVar7 != 0) {
      if (*(char *)(param_3 + 3) == '\x05') {
        FUN_140172420(&local_50,lVar7);
      }
      else {
        FUN_1401724a0(&local_50,*(undefined8 *)(param_2 + 0x68),param_3[1],lVar7);
      }
      local_160 = (undefined8 *)&DAT_1405a8b48;
      local_168 = (undefined8 **)&DAT_1405a8b28;
      (**(code **)(*param_1 + 0x60))(param_1,param_2,local_78,local_a0);
      local_128 = sony_zhacai::ZcRectT<int>::vftable;
      local_120 = *(undefined4 *)(param_2 + 0x30);
      local_11c = *(undefined4 *)(param_2 + 0x34);
      local_118 = *(undefined4 *)(param_2 + 0x38);
      local_114 = *(undefined4 *)(param_2 + 0x3c);
      local_160 = (undefined8 *)(lVar7 + 0x38);
      local_158 = (undefined8 *)CONCAT62(local_158._2_6_,*(undefined2 *)(lVar7 + 0x40));
      local_168 = &local_50;
      (**(code **)(*param_1 + 0x58))(param_1,&local_c0,lVar6,&local_128);
    }
    local_108 = operator_new(0x18);
    puVar12 = (undefined8 *)0x0;
    if (local_108 != (undefined8 *)0x0) {
      *local_108 = 0;
      local_108[1] = 0;
      *local_108 = 0;
      local_108[1] = 0;
      local_108[2] = 0;
      puVar12 = local_108;
    }
    FUN_1401869f0(puVar12,uVar1,uVar2);
    local_d8 = operator_new(0x18);
    if (local_d8 == (undefined8 *)0x0) {
      local_d8 = (undefined8 *)0x0;
    }
    else {
      *local_d8 = 0;
      local_d8[1] = 0;
      *local_d8 = 0;
      local_d8[1] = 0;
      local_d8[2] = 0;
    }
    puVar17 = local_d8;
    FUN_1401869f0(local_d8,uVar1,uVar2);
    local_50 = operator_new(0x18);
    if (local_50 == (undefined8 *)0x0) {
      local_50 = (undefined8 *)0x0;
    }
    else {
      *local_50 = 0;
      local_50[1] = 0;
      *local_50 = 0;
      local_50[1] = 0;
      local_50[2] = 0;
    }
    puVar18 = local_50;
    FUN_1401869f0(local_50,uVar1,uVar2);
    local_f0 = operator_new(0x18);
    if (local_f0 != (undefined8 *)0x0) {
      *local_f0 = 0;
      local_f0[1] = 0;
      *local_f0 = 0;
      local_f0[1] = 0;
      local_f0[2] = 0;
    }
    FUN_1401869f0(local_f0,uVar1,uVar2);
    local_e8 = operator_new(0x18);
    if (local_e8 != (undefined8 *)0x0) {
      *local_e8 = 0;
      local_e8[1] = 0;
      *local_e8 = 0;
      local_e8[1] = 0;
      local_e8[2] = 0;
    }
    FUN_1401869f0(local_e8,uVar1,uVar2);
    local_e0 = operator_new(0x18);
    if (local_e0 != (undefined8 *)0x0) {
      *local_e0 = 0;
      local_e0[1] = 0;
      *local_e0 = 0;
      local_e0[1] = 0;
      local_e0[2] = 0;
    }
    FUN_1401869f0(local_e0,uVar1,uVar2);
    local_128 = sony_zhacai::ZcRectT<int>::vftable;
    local_120 = *(undefined4 *)(local_f8 + 0x30);
    local_11c = *(undefined4 *)(local_f8 + 0x34);
    local_118 = *(undefined4 *)(local_f8 + 0x38);
    local_114 = *(undefined4 *)(local_f8 + 0x3c);
    local_138 = &DAT_1405a8b68;
    local_140 = local_e0;
    local_148 = &local_128;
    local_150 = (undefined ***)&DAT_1405a8b48;
    local_158 = (undefined8 *)&DAT_1405a8b28;
    local_160 = (undefined8 *)CONCAT44(local_160._4_4_,uVar2);
    local_168._0_4_ = uVar1;
    (**(code **)(*param_1 + 0x110))(param_1,local_f0,local_e8,local_c0);
    local_128 = sony_zhacai::ZcRectT<int>::vftable;
    local_120 = *(undefined4 *)(local_f8 + 0x30);
    local_11c = *(undefined4 *)(local_f8 + 0x34);
    local_118 = *(undefined4 *)(local_f8 + 0x38);
    local_114 = *(undefined4 *)(local_f8 + 0x3c);
    local_140 = (undefined8 *)local_a0;
    local_148 = (undefined ***)local_78;
    local_150 = &local_128;
    local_158 = local_e0;
    local_160 = local_f0;
    local_168._0_4_ = uVar2;
    (**(code **)(*param_1 + 0x118))(param_1,puVar12,local_c0,uVar1);
    local_128 = sony_zhacai::ZcRectT<int>::vftable;
    local_120 = *(undefined4 *)(local_f8 + 0x30);
    local_11c = *(undefined4 *)(local_f8 + 0x34);
    local_118 = *(undefined4 *)(local_f8 + 0x38);
    local_114 = *(undefined4 *)(local_f8 + 0x3c);
    local_148 = (undefined ***)CONCAT44(local_148._4_4_,DAT_1404df190);
    local_150 = &local_128;
    local_158 = local_e8;
    local_160 = (undefined8 *)CONCAT44(local_160._4_4_,uVar2);
    local_168 = (undefined8 **)CONCAT44(local_168._4_4_,uVar1);
    (**(code **)(*param_1 + 400))(param_1,puVar17,puVar18,local_c0);
    local_108 = operator_new(0x30);
    if (local_108 == (void *)0x0) {
      puVar8 = (undefined8 *)0x0;
    }
    else {
      puVar8 = (undefined8 *)FUN_140152660(local_108);
    }
    local_160 = (undefined8 *)((ulonglong)local_160 & 0xffffffff00000000);
    local_168 = (undefined8 **)((ulonglong)local_168 & 0xffffffffffffff00);
    local_d0 = puVar8;
    iVar5 = FUN_1401527f0(puVar8,1,*(undefined4 *)(lVar6 + 8),*(undefined4 *)(lVar6 + 0xc));
    if (iVar5 == 0) {
      lVar6 = FUN_140152710(puVar8,0);
      local_c8 = FUN_140152710(puVar8,1);
      local_108 = (undefined8 *)FUN_140152710(puVar8,2);
      fVar3 = DAT_1404df298;
      local_128 = sony_zhacai::ZcRectT<int>::vftable;
      local_120 = *(uint *)(local_f8 + 0x30);
      lVar7 = (longlong)(int)local_120;
      local_11c = *(uint *)(local_f8 + 0x34);
      local_118 = *(uint *)(local_f8 + 0x38);
      local_114 = *(uint *)(local_f8 + 0x3c);
      if (local_11c < local_114) {
        uVar16 = local_11c;
        do {
          puVar11 = (undefined2 *)
                    ((ulonglong)(uVar16 * *(int *)(lVar6 + 0x14)) + *(longlong *)(lVar6 + 0x20) +
                    (ulonglong)local_120 * 2);
          if (puVar17[2] == 0) {
            lVar15 = 0;
          }
          else if (uVar16 < *(uint *)((longlong)puVar17 + 4)) {
            lVar15 = (ulonglong)(uVar16 * *(int *)(puVar17 + 1)) + puVar17[2];
          }
          else {
            lVar15 = 0;
          }
          pfVar10 = (float *)(lVar7 * 4 + lVar15);
          if (puVar12[2] == 0) {
            lVar15 = 0;
          }
          else if (uVar16 < *(uint *)((longlong)puVar12 + 4)) {
            lVar15 = (ulonglong)(uVar16 * *(int *)(puVar12 + 1)) + puVar12[2];
          }
          else {
            lVar15 = 0;
          }
          if (local_120 < local_118) {
            lVar14 = lVar7 * 4 - (longlong)pfVar10;
            uVar13 = (ulonglong)(local_118 - local_120);
            do {
              fVar20 = *(float *)(lVar14 + lVar15 + (longlong)pfVar10) + *pfVar10;
              if (0.0 <= fVar20) {
                fVar19 = fVar3;
                if (fVar20 <= fVar3) {
                  fVar19 = fVar20;
                }
              }
              else {
                fVar19 = 0.0;
              }
              *puVar11 = (short)(int)fVar19;
              puVar11 = puVar11 + 1;
              pfVar10 = pfVar10 + 1;
              uVar13 = uVar13 - 1;
            } while (uVar13 != 0);
          }
          uVar16 = uVar16 + 1;
          puVar18 = local_50;
        } while (uVar16 < local_114);
      }
      if (local_11c < local_114) {
        uVar16 = local_11c;
        do {
          puVar11 = (undefined2 *)
                    ((ulonglong)(uVar16 * *(int *)(local_c8 + 0x14)) +
                     *(longlong *)(local_c8 + 0x20) + (ulonglong)local_120 * 2);
          if (puVar12[2] == 0) {
            lVar6 = 0;
          }
          else if (uVar16 < *(uint *)((longlong)puVar12 + 4)) {
            lVar6 = (ulonglong)(uVar16 * *(int *)(puVar12 + 1)) + puVar12[2];
          }
          else {
            lVar6 = 0;
          }
          pfVar10 = (float *)(lVar6 + lVar7 * 4);
          if (local_120 < local_118) {
            uVar13 = (ulonglong)(local_118 - local_120);
            do {
              fVar20 = *pfVar10;
              if (0.0 <= fVar20) {
                fVar19 = fVar3;
                if (fVar20 <= fVar3) {
                  fVar19 = fVar20;
                }
              }
              else {
                fVar19 = 0.0;
              }
              *puVar11 = (short)(int)fVar19;
              puVar11 = puVar11 + 1;
              pfVar10 = pfVar10 + 1;
              uVar13 = uVar13 - 1;
            } while (uVar13 != 0);
          }
          uVar16 = uVar16 + 1;
          puVar18 = local_50;
        } while (uVar16 < local_114);
      }
      if (local_11c < local_114) {
        uVar16 = local_11c;
        do {
          puVar11 = (undefined2 *)
                    ((ulonglong)(uVar16 * *(int *)((longlong)local_108 + 0x14)) +
                     *(longlong *)((longlong)local_108 + 0x20) + (ulonglong)local_120 * 2);
          if (puVar18[2] == 0) {
            lVar6 = 0;
          }
          else if (uVar16 < *(uint *)((longlong)puVar18 + 4)) {
            lVar6 = (ulonglong)(uVar16 * *(int *)(puVar18 + 1)) + puVar18[2];
          }
          else {
            lVar6 = 0;
          }
          pfVar10 = (float *)(lVar7 * 4 + lVar6);
          if (puVar12[2] == 0) {
            lVar6 = 0;
          }
          else if (uVar16 < *(uint *)((longlong)puVar12 + 4)) {
            lVar6 = (ulonglong)(uVar16 * *(int *)(puVar12 + 1)) + puVar12[2];
          }
          else {
            lVar6 = 0;
          }
          if (local_120 < local_118) {
            lVar15 = lVar7 * 4 - (longlong)pfVar10;
            uVar13 = (ulonglong)(local_118 - local_120);
            do {
              fVar20 = *(float *)((longlong)pfVar10 + lVar15 + lVar6) + *pfVar10;
              if (0.0 <= fVar20) {
                fVar19 = fVar3;
                if (fVar20 <= fVar3) {
                  fVar19 = fVar20;
                }
              }
              else {
                fVar19 = 0.0;
              }
              *puVar11 = (short)(int)fVar19;
              puVar11 = puVar11 + 1;
              pfVar10 = pfVar10 + 1;
              uVar13 = uVar13 - 1;
            } while (uVar13 != 0);
          }
          uVar16 = uVar16 + 1;
          puVar17 = local_d8;
        } while (uVar16 < local_114);
      }
      local_b8 = sony_zhacai::ZcRectT<int>::vftable;
      local_168 = (undefined8 **)CONCAT44(local_168._4_4_,10);
      local_110 = local_11c;
      local_100 = local_114;
      local_b0 = local_120;
      local_ac = local_11c;
      local_a8 = local_118;
      local_a4 = local_114;
      FUN_14012d2b0(&local_b8,10,10,10);
      lVar6 = local_f8;
      FUN_14016b640(local_f8,local_d0,0);
      *(uint *)(lVar6 + 0x30) = local_b0;
      *(uint *)(lVar6 + 0x38) = local_a8;
      *(uint *)(lVar6 + 0x34) = local_ac;
      *(uint *)(lVar6 + 0x3c) = local_a4;
      if (puVar12 != (undefined8 *)0x0) {
        lVar6 = puVar12[2];
        if (lVar6 != 0) {
          uVar9 = FUN_1401540a0();
          FUN_140153f60(uVar9,lVar6);
        }
        operator_delete(puVar12);
      }
      if (puVar17 != (undefined8 *)0x0) {
        lVar6 = puVar17[2];
        if (lVar6 != 0) {
          uVar9 = FUN_1401540a0();
          FUN_140153f60(uVar9,lVar6);
        }
        operator_delete(puVar17);
      }
      if (puVar18 != (undefined8 *)0x0) {
        lVar6 = puVar18[2];
        if (lVar6 != 0) {
          uVar9 = FUN_1401540a0();
          FUN_140153f60(uVar9,lVar6);
        }
        operator_delete(puVar18);
      }
      puVar12 = local_f0;
      if (local_f0 != (undefined8 *)0x0) {
        lVar6 = local_f0[2];
        if (lVar6 != 0) {
          uVar9 = FUN_1401540a0();
          FUN_140153f60(uVar9,lVar6);
        }
        operator_delete(puVar12);
      }
      puVar12 = local_e8;
      if (local_e8 != (undefined8 *)0x0) {
        lVar6 = local_e8[2];
        if (lVar6 != 0) {
          uVar9 = FUN_1401540a0();
          FUN_140153f60(uVar9,lVar6);
        }
        operator_delete(puVar12);
      }
      puVar12 = local_e0;
      if (local_e0 != (undefined8 *)0x0) {
        lVar6 = local_e0[2];
        if (lVar6 != 0) {
          uVar9 = FUN_1401540a0();
          FUN_140153f60(uVar9,lVar6);
        }
        operator_delete(puVar12);
      }
      pvVar4 = local_c0;
      if (local_c0 != (void *)0x0) {
        lVar6 = *(longlong *)((longlong)local_c0 + 0x10);
        if (lVar6 != 0) {
          uVar9 = FUN_1401540a0();
          FUN_140153f60(uVar9,lVar6);
        }
        operator_delete(pvVar4);
      }
      iVar5 = 0;
    }
    else if (puVar8 != (undefined8 *)0x0) {
      (**(code **)*puVar8)(puVar8,1);
    }
  }
  return iVar5;
}



// ===== depth0 FUN_14035ad60 @ 0x14035ad60 rva=0x35ad60 size=412 =====

/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

void FUN_14035ad60(undefined8 param_1,longlong *param_2,longlong param_3,undefined8 *param_4,
                  ulonglong *param_5,longlong param_6,ushort param_7)

{
  longlong lVar1;
  undefined1 auVar2 [14];
  undefined1 auVar3 [14];
  undefined1 auVar4 [14];
  undefined1 auVar5 [12];
  undefined1 auVar6 [14];
  float fVar7;
  undefined8 *puVar8;
  undefined8 *puVar9;
  uint uVar10;
  ulonglong uVar11;
  undefined8 *puVar12;
  uint uVar13;
  ulonglong uVar14;
  ushort *puVar15;
  float *pfVar16;
  uint uVar17;
  undefined1 auVar18 [16];
  undefined4 uVar22;
  float local_48 [8];
  undefined1 auVar19 [16];
  undefined1 auVar20 [16];
  undefined2 uVar21;
  
  uVar22 = CONCAT22(param_7,param_7);
  puVar8 = operator_new(0x18);
  puVar12 = (undefined8 *)0x0;
  puVar9 = puVar12;
  if (puVar8 != (undefined8 *)0x0) {
    *puVar8 = 0;
    puVar8[1] = 0;
    *puVar8 = 0;
    puVar8[1] = 0;
    puVar8[2] = 0;
    puVar9 = puVar8;
  }
  *param_2 = (longlong)puVar9;
  FUN_1401869f0(puVar9,*(undefined4 *)(param_3 + 8),*(undefined4 *)(param_3 + 0xc));
  fVar7 = DAT_1404deab0;
  uVar14 = *param_5;
  uVar21 = (undefined2)(uVar14 >> 0x30);
  auVar20._8_4_ = 0;
  auVar20._0_8_ = uVar14;
  auVar20._12_2_ = uVar21;
  auVar20._14_2_ = uVar21;
  uVar21 = (undefined2)(uVar14 >> 0x20);
  auVar19._12_4_ = auVar20._12_4_;
  auVar19._8_2_ = 0;
  auVar19._0_8_ = uVar14;
  auVar19._10_2_ = uVar21;
  auVar18._10_6_ = auVar19._10_6_;
  auVar18._8_2_ = uVar21;
  auVar18._0_8_ = uVar14;
  uVar21 = (undefined2)(uVar14 >> 0x10);
  auVar5._4_8_ = auVar18._8_8_;
  auVar5._2_2_ = uVar21;
  auVar5._0_2_ = uVar21;
  auVar2._8_4_ = 0;
  auVar2._0_8_ = CONCAT44(uVar22,uVar22);
  auVar2._12_2_ = param_7;
  auVar3._8_2_ = param_7;
  auVar3._0_8_ = CONCAT44(uVar22,uVar22);
  auVar3._10_4_ = auVar2._10_4_;
  auVar6._6_8_ = 0;
  auVar6._0_6_ = auVar3._8_6_;
  auVar4._4_2_ = param_7;
  auVar4._0_4_ = uVar22;
  auVar4._6_8_ = SUB148(auVar6 << 0x40,6);
  local_48[0] = (float)(int)(short)uVar14 * (float)param_7 * _DAT_140466990;
  local_48[1] = (float)(auVar5._0_4_ >> 0x10) * (float)auVar4._4_4_ * _UNK_140466994;
  local_48[2] = (float)(auVar18._8_4_ >> 0x10) * (float)auVar3._8_4_ * _UNK_140466998;
  local_48[3] = (float)(auVar19._12_4_ >> 0x10) * (float)(auVar2._10_4_ >> 0x10) * _UNK_14046699c;
  uVar17 = *(uint *)((longlong)param_4 + 0xc);
  if (uVar17 < *(uint *)((longlong)param_4 + 0x14)) {
    lVar1 = *param_2;
    uVar10 = *(uint *)(param_4 + 2);
    do {
      uVar13 = *(uint *)(param_4 + 1);
      uVar14 = (ulonglong)uVar13;
      puVar15 = (ushort *)
                ((ulonglong)(uVar17 * *(int *)(param_3 + 0x14)) + uVar14 * 2 +
                *(longlong *)(param_3 + 0x20));
      puVar9 = puVar12;
      if ((*(longlong *)(lVar1 + 0x10) != 0) && (uVar17 < *(uint *)(lVar1 + 4))) {
        puVar9 = (undefined8 *)
                 ((ulonglong)(uVar17 * *(int *)(lVar1 + 8)) + *(longlong *)(lVar1 + 0x10));
      }
      pfVar16 = (float *)((longlong)puVar9 + (longlong)(int)uVar13 * 4);
      if (uVar13 < uVar10) {
        do {
          uVar11 = (ulonglong)((uint)uVar14 & 1 | (uVar17 & 1) * 2);
          *pfVar16 = (float)(int)((uint)*puVar15 - (uint)*(ushort *)(param_6 + uVar11 * 2)) * fVar7
                     * local_48[uVar11];
          puVar15 = puVar15 + 1;
          pfVar16 = pfVar16 + 1;
          uVar13 = (uint)uVar14 + 1;
          uVar14 = (ulonglong)uVar13;
          uVar10 = *(uint *)(param_4 + 2);
        } while (uVar13 < uVar10);
      }
      uVar17 = uVar17 + 1;
    } while (uVar17 < *(uint *)((longlong)param_4 + 0x14));
  }
  *param_4 = sony_zhacai::ZcRectT<int>::vftable;
  return;
}



// ===== depth0 FUN_14035b810 @ 0x14035b810 rva=0x35b810 size=89 =====

void FUN_14035b810(undefined8 param_1,undefined8 param_2,undefined4 *param_3,undefined4 *param_4)

{
  *param_3 = 0xbd800000;
  param_3[1] = 0;
  *(undefined8 *)(param_3 + 2) = 0x3e800000;
  *(undefined8 *)(param_3 + 4) = 0x3f200000;
  *(undefined8 *)(param_3 + 6) = 0x3e800000;
  param_3[8] = 0xbd800000;
  *param_4 = 0;
  param_4[1] = 0xbd640000;
  param_4[2] = 0;
  *(undefined8 *)(param_4 + 3) = 0x3f0e4000;
  *(undefined8 *)(param_4 + 5) = 0x3f0e4000;
  param_4[7] = 0xbd640000;
  param_4[8] = 0;
  return;
}



// ===== depth0 FUN_14035ecc0 @ 0x14035ecc0 rva=0x35ecc0 size=1272 =====

void FUN_14035ecc0(longlong *param_1,undefined8 param_2,undefined8 param_3,undefined8 param_4,
                  undefined4 param_5,undefined4 param_6,undefined8 param_7,undefined8 param_8,
                  undefined8 *param_9,undefined8 param_10,undefined8 param_11)

{
  longlong lVar1;
  undefined8 *puVar2;
  undefined8 *puVar3;
  undefined8 *puVar4;
  undefined8 *puVar5;
  undefined8 *puVar6;
  undefined8 uVar7;
  undefined8 *puVar8;
  undefined8 *puVar9;
  undefined **local_60;
  undefined4 local_58;
  undefined4 local_54;
  undefined4 local_50;
  undefined4 local_4c;
  
  puVar2 = operator_new(0x18);
  puVar8 = (undefined8 *)0x0;
  puVar9 = puVar8;
  if (puVar2 != (undefined8 *)0x0) {
    *puVar2 = 0;
    puVar2[1] = 0;
    *puVar2 = 0;
    puVar2[1] = 0;
    puVar2[2] = 0;
    puVar9 = puVar2;
  }
  FUN_1401869f0(puVar9,param_5,param_6);
  puVar3 = operator_new(0x18);
  puVar2 = puVar8;
  if (puVar3 != (undefined8 *)0x0) {
    *puVar3 = 0;
    puVar3[1] = 0;
    *puVar3 = 0;
    puVar3[1] = 0;
    puVar3[2] = 0;
    puVar2 = puVar3;
  }
  FUN_1401869f0(puVar2,param_5,param_6);
  puVar4 = operator_new(0x18);
  puVar3 = puVar8;
  if (puVar4 != (undefined8 *)0x0) {
    *puVar4 = 0;
    puVar4[1] = 0;
    *puVar4 = 0;
    puVar4[1] = 0;
    puVar4[2] = 0;
    puVar3 = puVar4;
  }
  FUN_1401869f0(puVar3,param_5,param_6);
  puVar5 = operator_new(0x18);
  puVar4 = puVar8;
  if (puVar5 != (undefined8 *)0x0) {
    *puVar5 = 0;
    puVar5[1] = 0;
    *puVar5 = 0;
    puVar5[1] = 0;
    puVar5[2] = 0;
    puVar4 = puVar5;
  }
  FUN_1401869f0(puVar4,param_5,param_6);
  puVar6 = operator_new(0x18);
  puVar5 = puVar8;
  if (puVar6 != (undefined8 *)0x0) {
    *puVar6 = 0;
    puVar6[1] = 0;
    *puVar6 = 0;
    puVar6[1] = 0;
    puVar6[2] = 0;
    puVar5 = puVar6;
  }
  FUN_1401869f0(puVar5,param_5,param_6);
  puVar6 = operator_new(0x18);
  if (puVar6 != (undefined8 *)0x0) {
    *puVar6 = 0;
    puVar6[1] = 0;
    *puVar6 = 0;
    puVar6[1] = 0;
    puVar6[2] = 0;
    puVar8 = puVar6;
  }
  FUN_1401869f0(puVar8,param_5,param_6);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_9 + 1);
  local_54 = *(undefined4 *)((longlong)param_9 + 0xc);
  local_50 = *(undefined4 *)(param_9 + 2);
  local_4c = *(undefined4 *)((longlong)param_9 + 0x14);
  (**(code **)(*param_1 + 0x68))(param_1,puVar9,puVar3,param_4,&local_60);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_9 + 1);
  local_54 = *(undefined4 *)((longlong)param_9 + 0xc);
  local_50 = *(undefined4 *)(param_9 + 2);
  local_4c = *(undefined4 *)((longlong)param_9 + 0x14);
  (**(code **)(*param_1 + 0xa0))(param_1,puVar5,puVar3,&local_60);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_9 + 1);
  local_54 = *(undefined4 *)((longlong)param_9 + 0xc);
  local_50 = *(undefined4 *)(param_9 + 2);
  local_4c = *(undefined4 *)((longlong)param_9 + 0x14);
  (**(code **)(*param_1 + 0x70))(param_1,puVar2,puVar4,param_4,&local_60);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_9 + 1);
  local_54 = *(undefined4 *)((longlong)param_9 + 0xc);
  local_50 = *(undefined4 *)(param_9 + 2);
  local_4c = *(undefined4 *)((longlong)param_9 + 0x14);
  (**(code **)(*param_1 + 0xa0))(param_1,puVar8,puVar4,&local_60);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_9 + 1);
  local_54 = *(undefined4 *)((longlong)param_9 + 0xc);
  local_50 = *(undefined4 *)(param_9 + 2);
  local_4c = *(undefined4 *)((longlong)param_9 + 0x14);
  (**(code **)(*param_1 + 0xb0))(param_1,param_2,puVar5,puVar8,0,&local_60,param_7);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_9 + 1);
  local_54 = *(undefined4 *)((longlong)param_9 + 0xc);
  local_50 = *(undefined4 *)(param_9 + 2);
  local_4c = *(undefined4 *)((longlong)param_9 + 0x14);
  (**(code **)(*param_1 + 0xb0))(param_1,param_3,puVar5,puVar8,0,&local_60,param_8);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_9 + 1);
  local_54 = *(undefined4 *)((longlong)param_9 + 0xc);
  local_50 = *(undefined4 *)(param_9 + 2);
  local_4c = *(undefined4 *)((longlong)param_9 + 0x14);
  (**(code **)(*param_1 + 0xb8))(param_1,param_10,puVar5,puVar8,&local_60,param_11);
  if (puVar5 != (undefined8 *)0x0) {
    lVar1 = puVar5[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar5);
  }
  if (puVar8 != (undefined8 *)0x0) {
    lVar1 = puVar8[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar8);
  }
  if (puVar9 != (undefined8 *)0x0) {
    lVar1 = puVar9[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar9);
  }
  if (puVar2 != (undefined8 *)0x0) {
    lVar1 = puVar2[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar2);
  }
  if (puVar3 != (undefined8 *)0x0) {
    lVar1 = puVar3[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar3);
  }
  if (puVar4 != (undefined8 *)0x0) {
    lVar1 = puVar4[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar4);
  }
  *param_9 = sony_zhacai::ZcRectT<int>::vftable;
  return;
}



// ===== depth0 FUN_140362df0 @ 0x140362df0 rva=0x362df0 size=1793 =====

void FUN_140362df0(longlong *param_1,undefined8 param_2,undefined8 param_3,undefined4 param_4,
                  undefined4 param_5,undefined8 param_6,undefined8 param_7,undefined8 *param_8,
                  undefined8 param_9,undefined8 param_10)

{
  longlong lVar1;
  undefined8 *puVar2;
  undefined8 *puVar3;
  undefined8 *puVar4;
  undefined8 *puVar5;
  undefined8 *puVar6;
  undefined8 uVar7;
  undefined8 *puVar8;
  undefined8 *puVar9;
  undefined8 *local_78;
  undefined8 *local_70;
  undefined **local_60;
  undefined4 local_58;
  undefined4 local_54;
  undefined4 local_50;
  undefined4 local_4c;
  
  puVar2 = operator_new(0x18);
  puVar8 = (undefined8 *)0x0;
  puVar9 = puVar8;
  if (puVar2 != (undefined8 *)0x0) {
    *puVar2 = 0;
    puVar2[1] = 0;
    *puVar2 = 0;
    puVar2[1] = 0;
    puVar2[2] = 0;
    puVar9 = puVar2;
  }
  FUN_1401869f0(puVar9,param_4,param_5);
  puVar3 = operator_new(0x18);
  puVar2 = puVar8;
  if (puVar3 != (undefined8 *)0x0) {
    *puVar3 = 0;
    puVar3[1] = 0;
    *puVar3 = 0;
    puVar3[1] = 0;
    puVar3[2] = 0;
    puVar2 = puVar3;
  }
  FUN_1401869f0(puVar2,param_4,param_5);
  puVar4 = operator_new(0x18);
  puVar3 = puVar8;
  if (puVar4 != (undefined8 *)0x0) {
    *puVar4 = 0;
    puVar4[1] = 0;
    *puVar4 = 0;
    puVar4[1] = 0;
    puVar4[2] = 0;
    puVar3 = puVar4;
  }
  FUN_1401869f0(puVar3,param_4,param_5);
  puVar5 = operator_new(0x18);
  puVar4 = puVar8;
  if (puVar5 != (undefined8 *)0x0) {
    *puVar5 = 0;
    puVar5[1] = 0;
    *puVar5 = 0;
    puVar5[1] = 0;
    puVar5[2] = 0;
    puVar4 = puVar5;
  }
  FUN_1401869f0(puVar4,param_4,param_5);
  puVar6 = operator_new(0x18);
  puVar5 = puVar8;
  if (puVar6 != (undefined8 *)0x0) {
    *puVar6 = 0;
    puVar6[1] = 0;
    *puVar6 = 0;
    puVar6[1] = 0;
    puVar6[2] = 0;
    puVar5 = puVar6;
  }
  FUN_1401869f0(puVar5,param_4,param_5);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0xf0))(param_1,puVar9,puVar2,param_3,&local_60);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0xe8))(param_1,puVar3,puVar4,param_3,&local_60,param_9,param_10);
  local_70 = operator_new(0x18);
  if (local_70 == (undefined8 *)0x0) {
    local_70 = (undefined8 *)0x0;
  }
  else {
    *local_70 = 0;
    local_70[1] = 0;
    *local_70 = 0;
    local_70[1] = 0;
    local_70[2] = 0;
  }
  FUN_1401869f0(local_70,param_4,param_5);
  local_78 = operator_new(0x18);
  if (local_78 == (undefined8 *)0x0) {
    local_78 = (undefined8 *)0x0;
  }
  else {
    *local_78 = 0;
    local_78[1] = 0;
    *local_78 = 0;
    local_78[1] = 0;
    local_78[2] = 0;
  }
  FUN_1401869f0(local_78,param_4,param_5);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0xd0))(param_1,local_70,local_78,puVar9,puVar2,puVar3,puVar4,&local_60);
  if (puVar9 != (undefined8 *)0x0) {
    lVar1 = puVar9[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar9);
  }
  if (puVar2 != (undefined8 *)0x0) {
    lVar1 = puVar2[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar2);
  }
  if (puVar3 != (undefined8 *)0x0) {
    lVar1 = puVar3[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar3);
  }
  if (puVar4 != (undefined8 *)0x0) {
    lVar1 = puVar4[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar4);
  }
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0xf8))(param_1,puVar5,local_70,local_78,param_6,&local_60);
  if (local_70 != (undefined8 *)0x0) {
    lVar1 = local_70[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(local_70);
  }
  if (local_78 != (undefined8 *)0x0) {
    lVar1 = local_78[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(local_78);
  }
  puVar2 = operator_new(0x18);
  puVar9 = puVar8;
  if (puVar2 != (undefined8 *)0x0) {
    *puVar2 = 0;
    puVar2[1] = 0;
    *puVar2 = 0;
    puVar2[1] = 0;
    puVar2[2] = 0;
    puVar9 = puVar2;
  }
  FUN_1401869f0(puVar9,param_4,param_5);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  FUN_140364c20(puVar9,param_3,&local_60);
  puVar3 = operator_new(0x18);
  puVar2 = puVar8;
  if (puVar3 != (undefined8 *)0x0) {
    *puVar3 = 0;
    puVar3[1] = 0;
    *puVar3 = 0;
    puVar3[1] = 0;
    puVar3[2] = 0;
    puVar2 = puVar3;
  }
  FUN_1401869f0(puVar2,param_4,param_5);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0xc0))(param_1,puVar2,puVar9,&local_60);
  puVar3 = operator_new(0x18);
  if (puVar3 != (undefined8 *)0x0) {
    *puVar3 = 0;
    puVar3[1] = 0;
    *puVar3 = 0;
    puVar3[1] = 0;
    puVar3[2] = 0;
    puVar8 = puVar3;
  }
  FUN_1401869f0(puVar8,param_4,param_5);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 200))(param_1,puVar8,puVar9,&local_60,&DAT_1405a8b68);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0x108))(param_1,param_2,puVar5,puVar2,param_7,puVar8,&local_60);
  if (puVar5 != (undefined8 *)0x0) {
    lVar1 = puVar5[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar5);
  }
  if (puVar9 != (undefined8 *)0x0) {
    lVar1 = puVar9[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar9);
  }
  if (puVar2 != (undefined8 *)0x0) {
    lVar1 = puVar2[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar2);
  }
  if (puVar8 != (undefined8 *)0x0) {
    lVar1 = puVar8[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar8);
  }
  *param_8 = sony_zhacai::ZcRectT<int>::vftable;
  return;
}



// ===== depth0 FUN_140363500 @ 0x140363500 rva=0x363500 size=1490 =====

void FUN_140363500(longlong *param_1,undefined8 param_2,undefined8 param_3,undefined8 param_4,
                  undefined4 param_5,undefined4 param_6,undefined8 param_7,undefined8 *param_8)

{
  longlong lVar1;
  undefined8 *puVar2;
  undefined8 *puVar3;
  undefined8 *puVar4;
  undefined8 *puVar5;
  undefined8 *puVar6;
  undefined8 uVar7;
  undefined8 *puVar8;
  undefined8 *puVar9;
  undefined8 *local_78;
  undefined8 *local_70;
  undefined **local_60;
  undefined4 local_58;
  undefined4 local_54;
  undefined4 local_50;
  undefined4 local_4c;
  
  puVar2 = operator_new(0x18);
  puVar8 = (undefined8 *)0x0;
  puVar9 = puVar8;
  if (puVar2 != (undefined8 *)0x0) {
    *puVar2 = 0;
    puVar2[1] = 0;
    *puVar2 = 0;
    puVar2[1] = 0;
    puVar2[2] = 0;
    puVar9 = puVar2;
  }
  FUN_1401869f0(puVar9,param_5,param_6);
  puVar3 = operator_new(0x18);
  puVar2 = puVar8;
  if (puVar3 != (undefined8 *)0x0) {
    *puVar3 = 0;
    puVar3[1] = 0;
    *puVar3 = 0;
    puVar3[1] = 0;
    puVar3[2] = 0;
    puVar2 = puVar3;
  }
  FUN_1401869f0(puVar2,param_5,param_6);
  puVar4 = operator_new(0x18);
  puVar3 = puVar8;
  if (puVar4 != (undefined8 *)0x0) {
    *puVar4 = 0;
    puVar4[1] = 0;
    *puVar4 = 0;
    puVar4[1] = 0;
    puVar4[2] = 0;
    puVar3 = puVar4;
  }
  FUN_1401869f0(puVar3,param_5,param_6);
  local_70 = operator_new(0x18);
  if (local_70 == (undefined8 *)0x0) {
    local_70 = (undefined8 *)0x0;
  }
  else {
    *local_70 = 0;
    local_70[1] = 0;
    *local_70 = 0;
    local_70[1] = 0;
    local_70[2] = 0;
  }
  FUN_1401869f0(local_70,param_5,param_6);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0x150))(param_1,puVar9,param_4,&local_60);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0x158))(param_1,puVar2,param_4,&local_60);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0x160))(param_1,puVar3,param_4,&local_60);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0x168))(param_1,local_70,param_4,&local_60);
  local_78 = operator_new(0x18);
  if (local_78 == (undefined8 *)0x0) {
    local_78 = (undefined8 *)0x0;
  }
  else {
    *local_78 = 0;
    local_78[1] = 0;
    *local_78 = 0;
    local_78[1] = 0;
    local_78[2] = 0;
  }
  FUN_1401869f0(local_78,param_5,param_6);
  puVar5 = operator_new(0x18);
  puVar4 = puVar8;
  if (puVar5 != (undefined8 *)0x0) {
    *puVar5 = 0;
    puVar5[1] = 0;
    *puVar5 = 0;
    puVar5[1] = 0;
    puVar5[2] = 0;
    puVar4 = puVar5;
  }
  FUN_1401869f0(puVar4,param_5,param_6);
  puVar6 = operator_new(0x18);
  puVar5 = puVar8;
  if (puVar6 != (undefined8 *)0x0) {
    *puVar6 = 0;
    puVar6[1] = 0;
    *puVar6 = 0;
    puVar6[1] = 0;
    puVar6[2] = 0;
    puVar5 = puVar6;
  }
  FUN_1401869f0(puVar5,param_5,param_6);
  puVar6 = operator_new(0x18);
  if (puVar6 != (undefined8 *)0x0) {
    *puVar6 = 0;
    puVar6[1] = 0;
    *puVar6 = 0;
    puVar6[1] = 0;
    puVar6[2] = 0;
    puVar8 = puVar6;
  }
  FUN_1401869f0(puVar8,param_5,param_6);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0x178))
            (param_1,local_78,puVar4,puVar5,puVar8,puVar9,puVar2,puVar3,local_70,&local_60,param_4);
  if (puVar9 != (undefined8 *)0x0) {
    lVar1 = puVar9[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar9);
  }
  if (puVar2 != (undefined8 *)0x0) {
    lVar1 = puVar2[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar2);
  }
  if (puVar3 != (undefined8 *)0x0) {
    lVar1 = puVar3[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar3);
  }
  if (local_70 != (undefined8 *)0x0) {
    lVar1 = local_70[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(local_70);
  }
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0x180))(param_1,param_2,local_78,puVar4,param_7,&local_60);
  local_60 = sony_zhacai::ZcRectT<int>::vftable;
  local_58 = *(undefined4 *)(param_8 + 1);
  local_54 = *(undefined4 *)((longlong)param_8 + 0xc);
  local_50 = *(undefined4 *)(param_8 + 2);
  local_4c = *(undefined4 *)((longlong)param_8 + 0x14);
  (**(code **)(*param_1 + 0x180))(param_1,param_3,puVar5,puVar8,param_7,&local_60);
  if (local_78 != (undefined8 *)0x0) {
    lVar1 = local_78[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(local_78);
  }
  if (puVar4 != (undefined8 *)0x0) {
    lVar1 = puVar4[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar4);
  }
  if (puVar5 != (undefined8 *)0x0) {
    lVar1 = puVar5[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar5);
  }
  if (puVar8 != (undefined8 *)0x0) {
    lVar1 = puVar8[2];
    if (lVar1 != 0) {
      uVar7 = FUN_1401540a0();
      FUN_140153f60(uVar7,lVar1);
    }
    operator_delete(puVar8);
  }
  *param_8 = sony_zhacai::ZcRectT<int>::vftable;
  return;
}



// ===== depth0 FUN_140360910 @ 0x140360910 rva=0x360910 size=397 =====

void FUN_140360910(undefined8 param_1,longlong param_2,longlong param_3,longlong param_4,
                  longlong param_5,longlong param_6,undefined8 *param_7)

{
  float fVar1;
  float fVar2;
  float *pfVar3;
  uint uVar4;
  float *pfVar5;
  longlong lVar6;
  longlong lVar7;
  longlong lVar8;
  longlong lVar9;
  uint uVar10;
  longlong lVar11;
  float fVar12;
  
  fVar2 = DAT_1404debbc;
  uVar4 = *(uint *)((longlong)param_7 + 0xc);
  if (uVar4 < *(uint *)((longlong)param_7 + 0x14)) {
    uVar10 = *(uint *)(param_7 + 1);
    do {
      if (*(longlong *)(param_2 + 0x10) == 0) {
        lVar6 = 0;
      }
      else if (uVar4 < *(uint *)(param_2 + 4)) {
        lVar6 = (ulonglong)(uVar4 * *(int *)(param_2 + 8)) + *(longlong *)(param_2 + 0x10);
      }
      else {
        lVar6 = 0;
      }
      if (*(longlong *)(param_3 + 0x10) == 0) {
        lVar7 = 0;
      }
      else if (uVar4 < *(uint *)(param_3 + 4)) {
        lVar7 = (ulonglong)(uVar4 * *(int *)(param_3 + 8)) + *(longlong *)(param_3 + 0x10);
      }
      else {
        lVar7 = 0;
      }
      if (*(longlong *)(param_4 + 0x10) == 0) {
        lVar9 = 0;
      }
      else if (uVar4 < *(uint *)(param_4 + 4)) {
        lVar9 = (ulonglong)(uVar4 * *(int *)(param_4 + 8)) + *(longlong *)(param_4 + 0x10);
      }
      else {
        lVar9 = 0;
      }
      if (*(longlong *)(param_5 + 0x10) == 0) {
        lVar11 = 0;
      }
      else if (uVar4 < *(uint *)(param_5 + 4)) {
        lVar11 = (ulonglong)(uVar4 * *(int *)(param_5 + 8)) + *(longlong *)(param_5 + 0x10);
      }
      else {
        lVar11 = 0;
      }
      pfVar5 = (float *)(lVar11 + (longlong)(int)uVar10 * 4);
      if (*(longlong *)(param_6 + 0x10) == 0) {
        lVar8 = 0;
      }
      else if (uVar4 < *(uint *)(param_6 + 4)) {
        lVar8 = (ulonglong)(uVar4 * *(int *)(param_6 + 8)) + *(longlong *)(param_6 + 0x10);
      }
      else {
        lVar8 = 0;
      }
      if (uVar10 < *(uint *)(param_7 + 2)) {
        pfVar3 = (float *)((lVar7 - lVar11) + (longlong)pfVar5);
        do {
          fVar1 = *(float *)((longlong)pfVar3 + (lVar8 - lVar7));
          fVar12 = *pfVar5;
          if (*pfVar5 <= fVar1) {
            fVar12 = fVar1;
          }
          pfVar5 = pfVar5 + 1;
          uVar10 = uVar10 + 1;
          *(float *)((lVar6 - lVar7) + (longlong)pfVar3) =
               fVar12 * *(float *)((lVar9 - lVar7) + (longlong)pfVar3) + (fVar2 - fVar12) * *pfVar3;
          pfVar3 = pfVar3 + 1;
        } while (uVar10 < *(uint *)(param_7 + 2));
        uVar10 = *(uint *)(param_7 + 1);
      }
      uVar4 = uVar4 + 1;
    } while (uVar4 < *(uint *)((longlong)param_7 + 0x14));
  }
  *param_7 = sony_zhacai::ZcRectT<int>::vftable;
  return;
}



// ===== depth0 FUN_14035d3e0 @ 0x14035d3e0 rva=0x35d3e0 size=2016 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */
/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

void FUN_14035d3e0(undefined8 param_1,longlong param_2,longlong param_3,longlong param_4,
                  longlong param_5,undefined8 *param_6,char *param_7)

{
  uint *puVar1;
  uint uVar2;
  longlong lVar3;
  ulonglong uVar4;
  longlong lVar5;
  longlong lVar6;
  longlong lVar7;
  longlong lVar8;
  undefined8 uVar9;
  int iVar10;
  uint uVar11;
  undefined4 *puVar12;
  ulonglong uVar13;
  float *pfVar14;
  uint uVar15;
  int iVar16;
  int iVar17;
  ulonglong uVar18;
  longlong lVar19;
  int iVar20;
  int iVar21;
  uint uVar22;
  longlong lVar23;
  undefined4 *puVar24;
  undefined4 *puVar25;
  undefined4 *puVar26;
  undefined8 *puVar27;
  undefined4 uVar28;
  double dVar29;
  undefined1 auStack_168 [32];
  char *local_148;
  int local_138;
  undefined4 *local_130;
  undefined4 *local_128;
  uint local_120;
  longlong local_118;
  undefined4 *local_110;
  undefined4 *local_108;
  undefined8 *local_100;
  longlong local_f8;
  longlong local_f0;
  undefined4 *local_e8;
  undefined4 *local_e0;
  undefined4 *local_d8;
  undefined4 local_d0;
  undefined4 local_cc;
  undefined4 local_c8;
  undefined4 local_c4;
  undefined4 local_c0;
  undefined4 local_bc;
  undefined4 local_b8;
  undefined4 local_b4;
  undefined4 local_b0;
  longlong local_a8;
  undefined8 *local_a0;
  undefined4 local_98;
  undefined4 local_94;
  undefined4 local_90;
  undefined4 local_8c;
  undefined4 local_88;
  undefined4 local_84;
  undefined4 local_80;
  undefined4 local_7c;
  undefined4 local_78;
  undefined4 local_70;
  undefined4 local_6c;
  undefined4 local_68;
  undefined4 local_64;
  undefined4 local_60;
  undefined4 local_5c;
  undefined4 local_58;
  undefined4 local_54;
  undefined4 local_50;
  ulonglong local_48;
  
  local_48 = DAT_140559440 ^ (ulonglong)auStack_168;
  local_a0 = param_6;
  if (*param_7 == '\0') {
    uVar18 = 0;
    uVar22 = 0;
    iVar16 = *(int *)((longlong)param_6 + 0x14);
    iVar10 = *(int *)((longlong)param_6 + 0xc);
    if (iVar16 != iVar10) {
      iVar20 = *(int *)(param_6 + 1);
      lVar19 = *(longlong *)(param_2 + 0x10);
      iVar21 = *(int *)(param_6 + 2);
      uVar13 = uVar18;
      iVar17 = iVar20;
      do {
        uVar15 = iVar10 + (int)uVar13;
        uVar4 = uVar18;
        if ((*(longlong *)(param_4 + 0x10) != 0) && (uVar15 < *(uint *)(param_4 + 4))) {
          uVar4 = (ulonglong)(uVar15 * *(int *)(param_4 + 8)) + *(longlong *)(param_4 + 0x10);
        }
        lVar3 = (longlong)iVar20;
        puVar12 = (undefined4 *)(uVar4 + lVar3 * 4);
        uVar4 = uVar18;
        if ((lVar19 != 0) && (uVar15 < *(uint *)(param_2 + 4))) {
          uVar4 = (ulonglong)(uVar15 * *(int *)(param_2 + 8)) + lVar19;
        }
        if (iVar21 != iVar17) {
          lVar19 = uVar4 - (longlong)puVar12;
          uVar4 = uVar18;
          do {
            *(undefined4 *)(lVar19 + lVar3 * 4 + (longlong)puVar12) = *puVar12;
            puVar12 = puVar12 + 1;
            uVar15 = (int)uVar4 + 1;
            uVar4 = (ulonglong)uVar15;
            iVar21 = *(int *)(param_6 + 2);
            iVar20 = *(int *)(param_6 + 1);
          } while (uVar15 < (uint)(iVar21 - iVar20));
          lVar19 = *(longlong *)(param_2 + 0x10);
          iVar17 = iVar20;
        }
        uVar15 = (int)uVar13 + 1;
        uVar13 = (ulonglong)uVar15;
        iVar16 = *(int *)((longlong)param_6 + 0x14);
        iVar10 = *(int *)((longlong)param_6 + 0xc);
      } while (uVar15 < (uint)(iVar16 - iVar10));
    }
    if (iVar16 != iVar10) {
      do {
        uVar15 = iVar10 + uVar22;
        uVar13 = uVar18;
        if ((*(longlong *)(param_5 + 0x10) != 0) && (uVar15 < *(uint *)(param_5 + 4))) {
          uVar13 = (ulonglong)(uVar15 * *(int *)(param_5 + 8)) + *(longlong *)(param_5 + 0x10);
        }
        lVar19 = (longlong)*(int *)(param_6 + 1) * 4;
        puVar12 = (undefined4 *)(uVar13 + lVar19);
        uVar13 = uVar18;
        if ((*(longlong *)(param_3 + 0x10) != 0) && (uVar15 < *(uint *)(param_3 + 4))) {
          uVar13 = (ulonglong)(uVar15 * *(int *)(param_3 + 8)) + *(longlong *)(param_3 + 0x10);
        }
        if (*(int *)(param_6 + 2) != *(int *)(param_6 + 1)) {
          lVar3 = uVar13 - (longlong)puVar12;
          uVar13 = uVar18;
          do {
            *(undefined4 *)(lVar3 + lVar19 + (longlong)puVar12) = *puVar12;
            puVar12 = puVar12 + 1;
            uVar15 = (int)uVar13 + 1;
            uVar13 = (ulonglong)uVar15;
          } while (uVar15 < (uint)(*(int *)(param_6 + 2) - *(int *)(param_6 + 1)));
        }
        uVar22 = uVar22 + 1;
        iVar10 = *(int *)((longlong)param_6 + 0xc);
      } while (uVar22 < (uint)(*(int *)((longlong)param_6 + 0x14) - iVar10));
    }
  }
  else {
    local_f8 = param_2;
    local_f0 = param_3;
    local_a8 = param_4;
    local_100 = operator_new(0x18);
    uVar18 = 0;
    if (local_100 == (undefined8 *)0x0) {
      local_100 = (undefined8 *)0x0;
    }
    else {
      *local_100 = 0;
      local_100[1] = 0;
      *local_100 = 0;
      local_100[1] = 0;
      local_100[2] = 0;
    }
    puVar27 = local_100;
    FUN_1401869f0(local_100,*(int *)(param_6 + 2) - *(int *)(param_6 + 1),
                  *(int *)((longlong)param_6 + 0x14) - *(int *)((longlong)param_6 + 0xc));
    uVar15 = _UNK_1404e02b4;
    uVar22 = _DAT_1404e02b0;
    iVar16 = *(int *)((longlong)param_6 + 0x14);
    iVar10 = *(int *)((longlong)param_6 + 0xc);
    if (iVar16 != iVar10) {
      iVar20 = *(int *)(param_6 + 1);
      uVar13 = uVar18;
      iVar21 = iVar20;
      do {
        uVar2 = (uint)uVar13;
        uVar11 = iVar10 + uVar2;
        uVar13 = uVar18;
        if ((*(longlong *)(param_4 + 0x10) != 0) && (uVar11 < *(uint *)(param_4 + 4))) {
          uVar13 = (ulonglong)(uVar11 * *(int *)(param_4 + 8)) + *(longlong *)(param_4 + 0x10);
        }
        lVar19 = (longlong)iVar20;
        uVar4 = uVar18;
        if ((*(longlong *)(param_5 + 0x10) != 0) && (uVar11 < *(uint *)(param_5 + 4))) {
          uVar4 = (ulonglong)(uVar11 * *(int *)(param_5 + 8)) + *(longlong *)(param_5 + 0x10);
        }
        pfVar14 = (float *)(uVar4 + lVar19 * 4);
        uVar4 = uVar18;
        if ((puVar27[2] != 0) && (uVar2 < *(uint *)((longlong)puVar27 + 4))) {
          uVar4 = (ulonglong)(uVar2 * *(int *)(puVar27 + 1)) + puVar27[2];
        }
        if (*(int *)(param_6 + 2) != iVar21) {
          lVar3 = uVar13 - (longlong)pfVar14;
          lVar23 = uVar4 - (longlong)pfVar14;
          uVar13 = uVar18;
          do {
            dVar29 = (double)*(float *)(lVar3 + lVar19 * 4 + (longlong)pfVar14);
            *(float *)(lVar23 + (longlong)pfVar14) =
                 (float)((double)CONCAT44((uint)((ulonglong)dVar29 >> 0x20) & uVar15,
                                          SUB84(dVar29,0) & uVar22) +
                        (double)CONCAT44((uint)((ulonglong)(double)*pfVar14 >> 0x20) & uVar15,
                                         SUB84((double)*pfVar14,0) & uVar22));
            pfVar14 = pfVar14 + 1;
            uVar11 = (int)uVar13 + 1;
            uVar13 = (ulonglong)uVar11;
            iVar20 = *(int *)(param_6 + 1);
            iVar21 = iVar20;
          } while (uVar11 < (uint)(*(int *)(param_6 + 2) - iVar20));
        }
        uVar13 = (ulonglong)(uVar2 + 1);
        iVar16 = *(int *)((longlong)param_6 + 0x14);
        iVar10 = *(int *)((longlong)param_6 + 0xc);
      } while (uVar2 + 1 < (uint)(iVar16 - iVar10));
    }
    local_120 = 6;
    if (6 < (iVar16 - iVar10) + -6) {
      do {
        lVar23 = 0;
        local_118 = puVar27[2];
        lVar19 = lVar23;
        lVar3 = lVar23;
        if (local_118 == 0) {
          local_118 = 0;
        }
        else {
          uVar22 = *(uint *)((longlong)puVar27 + 4);
          if (local_120 - 1 < uVar22) {
            lVar19 = (ulonglong)((local_120 - 1) * *(int *)(puVar27 + 1)) + local_118;
          }
          if (local_120 < uVar22) {
            lVar3 = (ulonglong)(local_120 * *(int *)(puVar27 + 1)) + local_118;
          }
          if (local_120 + 1 < uVar22) {
            local_118 = (ulonglong)((local_120 + 1) * *(int *)(puVar27 + 1)) + local_118;
          }
          else {
            local_118 = 0;
          }
        }
        uVar22 = iVar10 + local_120;
        lVar8 = lVar23;
        if (*(longlong *)(param_4 + 0x10) == 0) {
          puVar12 = (undefined4 *)((longlong)*(int *)(param_6 + 1) * 4 + 0x18);
          local_d8 = puVar12;
          local_110 = puVar12;
        }
        else {
          puVar1 = (uint *)(param_4 + 4);
          local_d8 = (undefined4 *)((longlong)*(int *)(param_6 + 1) * 4 + 0x18);
          local_110 = local_d8;
          if (uVar22 - 1 < *puVar1) {
            local_110 = (undefined4 *)
                        ((longlong)local_d8 +
                        *(longlong *)(param_4 + 0x10) +
                        (ulonglong)((uVar22 - 1) * *(int *)(param_4 + 8)));
          }
          puVar12 = local_d8;
          if (uVar22 < *puVar1) {
            puVar12 = (undefined4 *)
                      ((longlong)local_d8 +
                      *(longlong *)(param_4 + 0x10) + (ulonglong)(uVar22 * *(int *)(param_4 + 8)));
          }
          if (uVar22 + 1 < *puVar1) {
            lVar8 = (ulonglong)((uVar22 + 1) * *(int *)(param_4 + 8)) +
                    *(longlong *)(param_4 + 0x10);
          }
        }
        local_e8 = (undefined4 *)(lVar8 + (longlong)local_d8);
        uVar15 = iVar10 + -1 + local_120;
        lVar8 = *(longlong *)(param_5 + 0x10);
        lVar7 = lVar23;
        puVar24 = local_d8;
        local_130 = local_d8;
        if (lVar8 != 0) {
          lVar5 = lVar23;
          if (uVar15 < *(uint *)(param_5 + 4)) {
            lVar5 = (ulonglong)(uVar15 * *(int *)(param_5 + 8)) + lVar8;
          }
          lVar6 = lVar23;
          if (uVar22 < *(uint *)(param_5 + 4)) {
            lVar6 = (ulonglong)(uVar22 * *(int *)(param_5 + 8)) + lVar8;
          }
          puVar24 = (undefined4 *)((longlong)local_d8 + lVar6);
          local_130 = (undefined4 *)(lVar5 + (longlong)local_d8);
          if (uVar22 + 1 < *(uint *)(param_5 + 4)) {
            lVar7 = (ulonglong)((uVar22 + 1) * *(int *)(param_5 + 8)) +
                    *(longlong *)(param_5 + 0x10);
          }
        }
        local_e0 = (undefined4 *)((longlong)local_d8 + lVar7);
        lVar8 = lVar23;
        if ((*(longlong *)(local_f8 + 0x10) != 0) && (uVar22 < *(uint *)(local_f8 + 4))) {
          lVar8 = (ulonglong)(uVar22 * *(int *)(local_f8 + 8)) + *(longlong *)(local_f8 + 0x10);
          puVar27 = local_100;
        }
        local_128 = (undefined4 *)(lVar8 + (longlong)local_d8);
        if ((*(longlong *)(local_f0 + 0x10) != 0) && (uVar22 < *(uint *)(local_f0 + 4))) {
          lVar23 = (ulonglong)(uVar22 * *(int *)(local_f0 + 8)) + *(longlong *)(local_f0 + 0x10);
        }
        local_d8 = (undefined4 *)((longlong)local_d8 + lVar23);
        local_138 = 6;
        if (6 < (*(int *)(param_6 + 2) - *(int *)(param_6 + 1)) + -6) {
          local_108 = puVar24 + 1;
          local_130 = local_130 + 1;
          puVar25 = (undefined4 *)(lVar19 + 0x1c);
          lVar3 = lVar3 - lVar19;
          lVar23 = local_118 - lVar19;
          puVar26 = local_110;
          do {
            local_d0 = puVar25[-2];
            local_cc = puVar25[-1];
            local_c8 = *puVar25;
            local_c4 = *(undefined4 *)((longlong)puVar25 + lVar3 + -8);
            local_c0 = *(undefined4 *)((longlong)puVar25 + lVar3 + -4);
            local_bc = *(undefined4 *)((longlong)puVar25 + lVar3);
            local_b8 = *(undefined4 *)((longlong)puVar25 + lVar23 + -8);
            local_b4 = *(undefined4 *)((longlong)puVar25 + lVar23 + -4);
            local_b0 = *(undefined4 *)((longlong)puVar25 + lVar23);
            local_98 = puVar26[-1];
            local_94 = *puVar26;
            puVar26 = puVar26 + 1;
            local_90 = *puVar26;
            local_8c = puVar12[-1];
            local_88 = *puVar12;
            local_84 = puVar12[1];
            local_80 = local_e8[-1];
            local_7c = *local_e8;
            local_e8 = local_e8 + 1;
            local_78 = *local_e8;
            local_148 = param_7;
            uVar28 = FUN_14035f340(&local_98,&local_d0,puVar12,
                                   *(undefined4 *)((longlong)puVar25 + lVar3 + -4));
            *local_128 = uVar28;
            local_70 = local_130[-2];
            local_6c = local_130[-1];
            local_68 = *local_130;
            local_64 = local_108[-2];
            local_60 = *(undefined4 *)((longlong)puVar25 + (longlong)puVar24 + (-0x1c - lVar19));
            local_5c = *local_108;
            local_58 = local_e0[-1];
            local_54 = *local_e0;
            local_e0 = local_e0 + 1;
            local_50 = *local_e0;
            local_148 = param_7;
            uVar28 = FUN_14035f340(&local_70,&local_d0);
            *local_d8 = uVar28;
            puVar25 = puVar25 + 1;
            local_130 = local_130 + 1;
            local_108 = local_108 + 1;
            local_128 = local_128 + 1;
            local_d8 = local_d8 + 1;
            local_138 = local_138 + 1;
            puVar12 = puVar12 + 1;
            param_4 = local_a8;
            puVar27 = local_100;
          } while (local_138 < (*(int *)(param_6 + 2) - *(int *)(param_6 + 1)) + -6);
        }
        local_120 = local_120 + 1;
        iVar10 = *(int *)((longlong)param_6 + 0xc);
      } while ((int)local_120 < (*(int *)((longlong)param_6 + 0x14) - iVar10) + -6);
    }
    if (puVar27 != (undefined8 *)0x0) {
      lVar19 = puVar27[2];
      if (lVar19 != 0) {
        uVar9 = FUN_1401540a0();
        FUN_140153f60(uVar9,lVar19);
      }
      operator_delete(puVar27);
    }
  }
  *param_6 = sony_zhacai::ZcRectT<int>::vftable;
  return;
}



