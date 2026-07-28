// 5 functions, roots=0x388c00 0x386da0 0x1869f0 0x152710 0x168b60 depth=0

// ===== depth0 FUN_140388c00 @ 0x140388c00 rva=0x388c00 size=2548 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */
/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

undefined8 FUN_140388c00(undefined8 param_1,longlong param_2,longlong *param_3)

{
  uint uVar1;
  int iVar2;
  int iVar3;
  longlong *plVar4;
  float fVar5;
  float fVar6;
  uint *puVar7;
  uint *puVar8;
  ushort uVar9;
  longlong lVar10;
  float *pfVar11;
  undefined8 uVar12;
  float *pfVar13;
  float *pfVar14;
  float fVar15;
  ushort *puVar16;
  float *pfVar17;
  longlong lVar18;
  ushort *puVar19;
  uint uVar20;
  ulonglong uVar21;
  float *pfVar22;
  float *pfVar23;
  longlong lVar24;
  float fVar25;
  float *pfVar26;
  longlong lVar27;
  float *pfVar28;
  longlong lVar29;
  float *pfVar30;
  longlong lVar31;
  int iVar32;
  ushort *puVar33;
  float *pfVar34;
  longlong lVar35;
  float fVar36;
  float fVar37;
  float fVar38;
  float fVar39;
  float fVar40;
  float fVar41;
  float fVar42;
  float fVar43;
  float fVar44;
  float fVar45;
  undefined1 auStack_158 [32];
  undefined4 local_138;
  float local_128;
  uint *local_120;
  uint *local_118;
  float *local_110;
  longlong local_108;
  longlong local_100;
  undefined **local_f8;
  uint local_f0;
  uint local_ec;
  uint local_e8;
  uint local_e4;
  ulonglong local_e0;
  
  local_e0 = DAT_140559440 ^ (ulonglong)auStack_158;
  local_100 = param_2;
  local_108 = FUN_140152710(*(undefined8 *)(param_2 + 8),0);
  fVar15 = DAT_1404deae4;
  iVar2 = *(int *)(local_108 + 0xc);
  iVar32 = *(int *)(local_108 + 8);
  local_f8 = sony_zhacai::ZcRectT<int>::vftable;
  local_f0 = *(uint *)(param_2 + 0x30);
  local_ec = *(uint *)(param_2 + 0x34);
  local_e8 = *(uint *)(param_2 + 0x38);
  local_e4 = *(uint *)(param_2 + 0x3c);
  lVar10 = param_3[1];
  fVar44 = (float)(*(int *)(lVar10 + 0x20c) + 100) / DAT_1404df210;
  fVar45 = (float)(*(int *)(lVar10 + 0x210) + 100) / DAT_1404df210;
  iVar3 = *(int *)(lVar10 + 0x214);
  if (iVar3 < 0) {
    fVar42 = (float)(iVar3 + 100) * DAT_1404deae4;
  }
  else {
    fVar42 = (float)(iVar3 + 0x19);
  }
  fVar43 = fVar42 * DAT_1404df2cc;
  plVar4 = (longlong *)*param_3;
  lVar10 = (**(code **)(*plVar4 + 0xd8))(plVar4,*(undefined4 *)(lVar10 + 0x1e0));
  fVar6 = DAT_1404debbc;
  iVar3 = *(int *)(param_3[1] + 0x208);
  fVar40 = (float)*(int *)(param_3[1] + 0x2c4) / _DAT_14046679c;
  fVar41 = *(float *)((longlong)plVar4 + 0x2275c);
  fVar36 = DAT_1404debbc;
  if ((fVar41 != DAT_1404debbc) && (fVar36 = fVar41, *(int *)((longlong)plVar4 + 0x1ac) < iVar3)) {
    fVar37 = (float)(100 - iVar3) / (float)(100 - *(int *)((longlong)plVar4 + 0x1ac));
    fVar36 = DAT_1404debbc - fVar37;
    fVar36 = fVar36 + fVar36 + fVar41 * fVar37;
  }
  fVar41 = ((float)(iVar3 + 100) * DAT_1404deb28 * *(float *)(lVar10 + 0x1060) * fVar40) /
           DAT_140468bf0;
  if (fVar40 <= DAT_1404debbc) {
    fVar40 = (fVar40 - DAT_1404debbc) * DAT_140468be8 + fVar15;
  }
  else {
    fVar40 = fVar40 * fVar15;
  }
  local_138 = 2;
  (*(code *)local_f8[0xb])(&local_f8,2,2,2);
  local_118 = operator_new(0x18);
  pfVar14 = (float *)0x0;
  if (local_118 == (uint *)0x0) {
    local_118 = (uint *)0x0;
  }
  else {
    local_118[0] = 0;
    local_118[1] = 0;
    local_118[2] = 0;
    local_118[3] = 0;
    local_118[0] = 0;
    local_118[1] = 0;
    local_118[2] = 0;
    local_118[3] = 0;
    local_118[4] = 0;
    local_118[5] = 0;
  }
  fVar15 = (float)(iVar2 + 6);
  iVar32 = iVar32 + 6;
  local_110 = (float *)CONCAT44(local_110._4_4_,iVar32);
  local_128 = fVar15;
  FUN_1401869f0(local_118,iVar32,fVar15);
  local_120 = operator_new(0x18);
  if (local_120 == (uint *)0x0) {
    local_120 = (uint *)0x0;
  }
  else {
    local_120[0] = 0;
    local_120[1] = 0;
    local_120[2] = 0;
    local_120[3] = 0;
    local_120[0] = 0;
    local_120[1] = 0;
    local_120[2] = 0;
    local_120[3] = 0;
    local_120[4] = 0;
    local_120[5] = 0;
  }
  FUN_1401869f0(local_120,iVar32,fVar15);
  if (*(void **)(local_120 + 4) != (void *)0x0) {
    memset(*(void **)(local_120 + 4),0,(ulonglong)local_120[3]);
  }
  fVar25 = DAT_1404df1d0;
  fVar5 = DAT_1404df118;
  fVar37 = DAT_140468bec;
  if (local_ec < local_e4) {
    uVar20 = local_ec + 3;
    do {
      lVar10 = *(longlong *)(local_108 + 0x20);
      iVar2 = *(int *)(local_108 + 0x14);
      lVar18 = (ulonglong)local_f0 * 2;
      puVar16 = (ushort *)((ulonglong)((uVar20 - 4) * iVar2) + lVar18 + lVar10);
      puVar19 = (ushort *)((ulonglong)((uVar20 - 3) * iVar2) + lVar18 + lVar10);
      puVar33 = (ushort *)((ulonglong)((uVar20 - 2) * iVar2) + lVar18 + lVar10);
      if (*(longlong *)(local_120 + 4) == 0) {
        pfVar22 = (float *)&DAT_fffffffffffffff8;
      }
      else if (uVar20 < local_120[1]) {
        if (*local_120 < 4) {
          pfVar22 = (float *)&DAT_fffffffffffffff8;
        }
        else {
          pfVar22 = (float *)((ulonglong)(uVar20 * local_120[2]) + 4 + *(longlong *)(local_120 + 4))
          ;
        }
      }
      else {
        pfVar22 = (float *)&DAT_fffffffffffffff8;
      }
      pfVar13 = pfVar14;
      uVar1 = local_f0;
      if ((*(longlong *)(local_118 + 4) != 0) && (uVar20 < local_118[1])) {
        if (*local_118 < 4) {
          pfVar13 = (float *)0x0;
        }
        else {
          pfVar13 = (float *)((ulonglong)(uVar20 * local_118[2]) + 0xc +
                             *(longlong *)(local_118 + 4));
        }
      }
      for (; uVar1 < local_e8; uVar1 = uVar1 + 1) {
        fVar39 = (float)*puVar19;
        pfVar22[-1] = fVar39 + pfVar22[-1];
        fVar38 = fVar39 * fVar5;
        *pfVar22 = fVar38 + *pfVar22;
        fVar15 = fVar39 * fVar37;
        pfVar22[1] = fVar15 + pfVar22[1];
        pfVar22[2] = fVar39 * fVar25 + pfVar22[2];
        pfVar22[3] = fVar15 + pfVar22[3];
        pfVar22[4] = fVar38 + pfVar22[4];
        pfVar22[5] = fVar39 + pfVar22[5];
        pfVar22 = pfVar22 + 1;
        *pfVar13 = (float)((uint)puVar19[-1] + (uint)*puVar19 * 4 + (uint)*puVar16 +
                           (uint)puVar19[1] + (uint)*puVar33);
        puVar19 = puVar19 + 1;
        puVar16 = puVar16 + 1;
        puVar33 = puVar33 + 1;
        pfVar13 = pfVar13 + 1;
      }
      uVar1 = uVar20 - 2;
      uVar20 = uVar20 + 1;
    } while (uVar1 < local_e4);
    fVar15 = local_128;
    iVar32 = (int)local_110;
  }
  local_110 = operator_new(0x18);
  pfVar22 = pfVar14;
  if (local_110 != (float *)0x0) {
    local_110[0] = 0.0;
    local_110[1] = 0.0;
    local_110[2] = 0.0;
    local_110[3] = 0.0;
    local_110[0] = 0.0;
    local_110[1] = 0.0;
    local_110[2] = 0.0;
    local_110[3] = 0.0;
    local_110[4] = 0.0;
    local_110[5] = 0.0;
    pfVar22 = local_110;
  }
  FUN_1401869f0(pfVar22,iVar32,fVar15);
  if (*(void **)(pfVar22 + 4) != (void *)0x0) {
    memset(*(void **)(pfVar22 + 4),0,(ulonglong)(uint)pfVar22[3]);
  }
  puVar8 = local_118;
  if (local_ec < local_e4) {
    fVar15 = (float)(local_ec + 2);
    do {
      local_128 = fVar15;
      lVar10 = *(longlong *)(pfVar22 + 4);
      pfVar13 = pfVar14;
      pfVar17 = pfVar14;
      pfVar23 = pfVar14;
      pfVar26 = pfVar14;
      pfVar28 = pfVar14;
      pfVar30 = pfVar14;
      pfVar34 = pfVar14;
      if (lVar10 != 0) {
        if (((uint)((int)local_128 + -2) < (uint)pfVar22[1]) && (3 < (uint)*pfVar22)) {
          pfVar13 = (float *)((ulonglong)(uint)(((int)local_128 + -2) * (int)pfVar22[2]) + 0xc +
                             lVar10);
        }
        if (((uint)((int)local_128 + -1) < (uint)pfVar22[1]) && (3 < (uint)*pfVar22)) {
          pfVar30 = (float *)((ulonglong)(uint)(((int)local_128 + -1) * (int)pfVar22[2]) + 0xc +
                             lVar10);
        }
        if (((uint)local_128 < (uint)pfVar22[1]) && (3 < (uint)*pfVar22)) {
          pfVar28 = (float *)((ulonglong)(uint)((int)local_128 * (int)pfVar22[2]) + 0xc + lVar10);
        }
        if (((uint)((int)local_128 + 1) < (uint)pfVar22[1]) && (3 < (uint)*pfVar22)) {
          pfVar34 = (float *)((ulonglong)(uint)(((int)local_128 + 1) * (int)pfVar22[2]) + 0xc +
                             lVar10);
        }
        if (((uint)((int)local_128 + 2) < (uint)pfVar22[1]) && (3 < (uint)*pfVar22)) {
          pfVar17 = (float *)((ulonglong)(uint)(((int)local_128 + 2) * (int)pfVar22[2]) + 0xc +
                             lVar10);
        }
        if (((uint)((int)local_128 + 3) < (uint)pfVar22[1]) && (3 < (uint)*pfVar22)) {
          pfVar26 = (float *)((ulonglong)(uint)(((int)local_128 + 3) * (int)pfVar22[2]) + 0xc +
                             lVar10);
        }
        if ((uint)((int)local_128 + 4) < (uint)pfVar22[1]) {
          if ((uint)*pfVar22 < 4) {
            pfVar23 = (float *)0x0;
          }
          else {
            pfVar23 = (float *)((ulonglong)(uint)(((int)local_128 + 4) * (int)pfVar22[2]) + 0xc +
                               lVar10);
          }
        }
      }
      pfVar11 = pfVar14;
      if (((*(longlong *)(local_120 + 4) != 0) && ((int)local_128 + 1U < local_120[1])) &&
         (3 < *local_120)) {
        pfVar11 = (float *)((ulonglong)(((int)local_128 + 1U) * local_120[2]) + 0xc +
                           *(longlong *)(local_120 + 4));
      }
      if (local_f0 < local_e8) {
        lVar10 = (longlong)pfVar11 - (longlong)pfVar13;
        lVar31 = (longlong)pfVar30 - (longlong)pfVar13;
        lVar29 = (longlong)pfVar28 - (longlong)pfVar13;
        lVar35 = (longlong)pfVar34 - (longlong)pfVar13;
        lVar18 = (longlong)pfVar17 - (longlong)pfVar13;
        lVar27 = (longlong)pfVar26 - (longlong)pfVar13;
        lVar24 = (longlong)pfVar23 - (longlong)pfVar13;
        uVar20 = local_f0;
        do {
          fVar15 = *(float *)(lVar10 + (longlong)pfVar13);
          *pfVar13 = fVar15 + *pfVar13;
          fVar39 = fVar15 * fVar5;
          *(float *)((longlong)pfVar13 + lVar31) = fVar39 + *(float *)((longlong)pfVar13 + lVar31);
          fVar38 = fVar15 * fVar37;
          *(float *)(lVar29 + (longlong)pfVar13) = fVar38 + *(float *)(lVar29 + (longlong)pfVar13);
          *(float *)(lVar35 + (longlong)pfVar13) =
               fVar15 * fVar25 + *(float *)(lVar35 + (longlong)pfVar13);
          *(float *)(lVar18 + (longlong)pfVar13) = fVar38 + *(float *)(lVar18 + (longlong)pfVar13);
          *(float *)(lVar27 + (longlong)pfVar13) = fVar39 + *(float *)(lVar27 + (longlong)pfVar13);
          *(float *)(lVar24 + (longlong)pfVar13) = fVar15 + *(float *)(lVar24 + (longlong)pfVar13);
          pfVar13 = pfVar13 + 1;
          uVar20 = uVar20 + 1;
        } while (uVar20 < local_e8);
      }
      fVar15 = (float)((int)local_128 + 1);
    } while ((int)local_128 - 1U < local_e4);
  }
  fVar5 = DAT_1404deadc;
  fVar37 = DAT_140466950;
  fVar15 = DAT_1404667a0;
  if (local_ec < local_e4) {
    fVar25 = (float)(local_ec + 3);
    do {
      uVar21 = (ulonglong)local_f0;
      puVar16 = (ushort *)
                ((ulonglong)(uint)(((int)fVar25 + -3) * *(int *)(local_108 + 0x14)) + uVar21 * 2 +
                *(longlong *)(local_108 + 0x20));
      pfVar13 = pfVar14;
      if ((*(longlong *)(pfVar22 + 4) != 0) && ((uint)fVar25 < (uint)pfVar22[1])) {
        if ((uint)*pfVar22 < 4) {
          pfVar13 = (float *)0x0;
        }
        else {
          pfVar13 = (float *)(*(longlong *)(pfVar22 + 4) + 0xc +
                             (ulonglong)(uint)((int)fVar25 * (int)pfVar22[2]));
        }
      }
      pfVar17 = pfVar14;
      if ((*(longlong *)(local_118 + 4) != 0) && ((uint)fVar25 < local_118[1])) {
        if (*local_118 < 4) {
          pfVar17 = (float *)0x0;
        }
        else {
          pfVar17 = (float *)(*(longlong *)(local_118 + 4) + 0xc +
                             (ulonglong)((int)fVar25 * local_118[2]));
        }
      }
      if (local_f0 < local_e8) {
        lVar10 = (longlong)pfVar17 - (longlong)pfVar13;
        do {
          fVar38 = (float)*puVar16 -
                   (*(float *)(lVar10 + (longlong)pfVar13) * fVar5 * (fVar6 - fVar40) +
                   *pfVar13 * fVar37 * fVar40);
          if (fVar38 <= fVar42) {
            if (fVar43 <= fVar38) {
              fVar38 = 0.0;
            }
            else {
              fVar38 = fVar38 * fVar45;
            }
          }
          else {
            fVar38 = fVar38 * fVar44;
          }
          fVar38 = fVar38 * fVar41 * fVar36 + (float)*puVar16;
          if (0.0 <= fVar38) {
            uVar9 = 0x7fff;
            if (fVar38 <= fVar15) {
              uVar9 = (ushort)(int)fVar38;
            }
          }
          else {
            uVar9 = 0;
          }
          *puVar16 = uVar9;
          puVar16 = puVar16 + 1;
          pfVar13 = pfVar13 + 1;
          uVar20 = (int)uVar21 + 1;
          uVar21 = (ulonglong)uVar20;
        } while (uVar20 < local_e8);
      }
      uVar20 = (int)fVar25 - 2;
      fVar25 = (float)((int)fVar25 + 1);
    } while (uVar20 < local_e4);
  }
  lVar10 = *(longlong *)(pfVar22 + 4);
  if (lVar10 != 0) {
    uVar12 = FUN_1401540a0();
    FUN_140153f60(uVar12,lVar10);
  }
  operator_delete(pfVar22);
  puVar7 = local_120;
  lVar10 = *(longlong *)(local_120 + 4);
  if (lVar10 != 0) {
    uVar12 = FUN_1401540a0();
    FUN_140153f60(uVar12,lVar10);
  }
  operator_delete(puVar7);
  if (puVar8 != (uint *)0x0) {
    lVar10 = *(longlong *)(puVar8 + 4);
    if (lVar10 != 0) {
      uVar12 = FUN_1401540a0();
      FUN_140153f60(uVar12,lVar10);
    }
    operator_delete(puVar8);
  }
  *(uint *)(local_100 + 0x30) = local_f0;
  *(uint *)(local_100 + 0x38) = local_e8;
  *(uint *)(local_100 + 0x34) = local_ec;
  *(uint *)(local_100 + 0x3c) = local_e4;
  return 0;
}



// ===== depth0 FUN_140386da0 @ 0x140386da0 rva=0x386da0 size=7651 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */
/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

undefined8 FUN_140386da0(undefined8 param_1,longlong param_2,longlong *param_3)

{
  ushort *puVar1;
  int iVar2;
  int iVar3;
  longlong *plVar4;
  void *_Dst;
  float fVar5;
  undefined1 auVar6 [16];
  undefined1 auVar7 [16];
  float fVar8;
  undefined1 *puVar9;
  uint *puVar10;
  undefined2 uVar11;
  uint uVar12;
  longlong lVar13;
  longlong lVar14;
  ulonglong uVar15;
  undefined8 uVar16;
  undefined1 (*pauVar17) [32];
  undefined1 (*pauVar18) [32];
  undefined1 (*pauVar19) [16];
  undefined1 (*pauVar20) [32];
  undefined2 uVar21;
  undefined1 (*pauVar22) [32];
  uint uVar23;
  undefined1 (*pauVar24) [32];
  uint uVar25;
  longlong lVar26;
  float *pfVar27;
  undefined1 (*pauVar28) [32];
  longlong lVar29;
  undefined1 (*pauVar30) [32];
  undefined1 (*pauVar31) [32];
  longlong lVar32;
  undefined1 (*pauVar33) [16];
  undefined1 (*pauVar34) [32];
  longlong lVar35;
  uint uVar36;
  undefined1 (*pauVar37) [16];
  undefined1 (*pauVar38) [32];
  longlong lVar39;
  uint *puVar40;
  uint uVar41;
  int iVar42;
  undefined1 auVar43 [32];
  undefined1 auVar44 [32];
  undefined1 auVar45 [32];
  undefined1 auVar46 [32];
  undefined1 auVar47 [32];
  undefined1 auVar48 [32];
  undefined1 auVar49 [32];
  undefined1 auVar50 [32];
  undefined1 auVar51 [32];
  undefined1 auVar52 [32];
  undefined1 auVar53 [32];
  undefined1 auVar54 [32];
  undefined1 extraout_var [56];
  undefined1 auVar55 [64];
  float fVar56;
  undefined1 auVar57 [32];
  undefined1 auVar58 [32];
  undefined1 auVar59 [32];
  undefined1 auVar60 [32];
  undefined1 auVar61 [32];
  undefined1 auVar62 [32];
  undefined1 auVar63 [32];
  undefined1 auVar64 [32];
  undefined1 auVar65 [32];
  undefined1 auVar66 [32];
  undefined1 auVar67 [32];
  undefined1 auVar68 [32];
  undefined1 auVar69 [32];
  undefined1 auVar70 [32];
  undefined1 auVar71 [32];
  float fVar72;
  float fVar73;
  undefined1 auVar74 [32];
  undefined1 auVar75 [32];
  undefined1 auVar76 [32];
  undefined1 auVar77 [32];
  undefined1 auVar78 [32];
  undefined1 auVar79 [32];
  undefined1 auVar80 [32];
  undefined1 auVar81 [32];
  undefined1 auVar82 [32];
  undefined1 auVar83 [32];
  undefined1 auVar84 [32];
  undefined1 auVar85 [32];
  undefined1 auVar86 [64];
  undefined1 auVar87 [64];
  undefined1 auVar88 [64];
  undefined1 auVar89 [64];
  undefined1 auVar90 [64];
  undefined1 auVar91 [64];
  undefined1 auVar92 [64];
  undefined1 auVar93 [64];
  undefined1 auVar94 [64];
  undefined1 auVar95 [64];
  undefined1 auVar96 [64];
  undefined1 auVar97 [64];
  undefined1 auVar98 [32];
  undefined1 auVar99 [32];
  undefined1 auVar100 [32];
  undefined1 in_ZMM3 [64];
  undefined1 auVar101 [64];
  undefined1 auVar102 [64];
  float fVar103;
  undefined1 auVar104 [32];
  undefined1 auVar105 [32];
  undefined1 auVar106 [32];
  undefined1 auVar107 [32];
  undefined1 in_ZMM6 [64];
  undefined1 auVar108 [64];
  undefined1 auVar109 [64];
  undefined1 auVar110 [32];
  undefined1 auVar111 [32];
  undefined1 in_ZMM7 [64];
  float fVar112;
  float fVar113;
  float fVar114;
  float fVar115;
  float fVar116;
  float fVar117;
  float fVar118;
  undefined1 in_ZMM8 [64];
  undefined1 auVar119 [16];
  undefined1 in_ZMM10 [64];
  undefined1 auVar121 [60];
  undefined1 auVar120 [64];
  undefined1 auVar122 [16];
  undefined1 in_ZMM11 [64];
  undefined1 auVar124 [60];
  undefined1 auVar123 [64];
  float fVar125;
  float fVar126;
  undefined1 auVar127 [16];
  undefined1 in_ZMM12 [64];
  undefined1 auVar129 [60];
  undefined1 auVar128 [64];
  float fVar130;
  undefined1 auVar131 [16];
  undefined1 in_ZMM13 [64];
  undefined1 auVar132 [64];
  undefined1 auVar133 [64];
  float fVar134;
  float fVar135;
  undefined1 auStack_278 [32];
  undefined4 local_258;
  uint local_240;
  uint local_23c;
  float local_238;
  uint local_234;
  float local_230;
  uint *local_228;
  uint *local_220;
  float local_218;
  undefined1 (*local_210) [32];
  longlong local_208;
  longlong local_200;
  undefined1 local_1e0 [32];
  undefined1 local_1c0 [32];
  undefined1 local_1a0 [12];
  float fStack_194;
  float fStack_18c;
  undefined1 local_180 [32];
  float local_160;
  float fStack_15c;
  float fStack_158;
  float fStack_154;
  float fStack_150;
  float fStack_14c;
  float fStack_148;
  float fStack_144;
  undefined1 local_140 [12];
  float fStack_134;
  float fStack_12c;
  undefined **local_120;
  uint local_118;
  uint local_114;
  int local_110;
  uint local_10c;
  float local_108;
  ulonglong local_100;
  undefined1 local_b8 [16];
  undefined1 local_a8 [16];
  undefined1 local_98 [16];
  undefined1 local_88 [16];
  undefined1 local_68 [16];
  undefined1 local_58 [16];
  undefined1 local_48 [16];
  
  local_48 = in_ZMM6._0_16_;
  local_58 = in_ZMM7._0_16_;
  local_68 = in_ZMM8._0_16_;
  local_88 = in_ZMM10._0_16_;
  local_98 = in_ZMM11._0_16_;
  local_a8 = in_ZMM12._0_16_;
  local_b8 = in_ZMM13._0_16_;
  local_100 = DAT_140559440 ^ (ulonglong)auStack_278;
  local_200 = param_2;
  lVar13 = FUN_140152710(*(undefined8 *)(param_2 + 8),0);
  iVar2 = *(int *)(lVar13 + 0xc);
  iVar42 = *(int *)(lVar13 + 8);
  local_120 = sony_zhacai::ZcRectT<int>::vftable;
  local_118 = *(uint *)(param_2 + 0x30);
  local_114 = *(uint *)(param_2 + 0x34);
  local_110 = *(int *)(param_2 + 0x38);
  local_10c = *(uint *)(param_2 + 0x3c);
  lVar14 = param_3[1];
  local_108 = (float)(*(int *)(lVar14 + 0x20c) + 100) / DAT_1404df210;
  local_230 = (float)(*(int *)(lVar14 + 0x210) + 100) / DAT_1404df210;
  iVar3 = *(int *)(lVar14 + 0x214);
  auVar108._16_48_ = in_ZMM6._16_48_;
  auVar108._0_16_ = ZEXT416((uint)DAT_1404deae4);
  auVar109._16_48_ = extraout_var._8_48_;
  if (iVar3 < 0) {
    auVar109._0_16_ = ZEXT416((uint)(float)(iVar3 + 100));
    auVar55._4_60_ = auVar109._4_60_;
    auVar55._0_4_ = (float)(iVar3 + 100) * DAT_1404deae4;
    auVar120._0_16_ = auVar55._0_16_;
  }
  else {
    auVar120._0_16_ = ZEXT416((uint)(float)(iVar3 + 0x19));
  }
  local_238 = auVar120._0_4_;
  auVar132._16_48_ = in_ZMM13._16_48_;
  auVar132._0_16_ = auVar120._0_16_;
  auVar133._4_60_ = auVar132._4_60_;
  auVar133._0_4_ = local_238 * DAT_1404df2cc;
  plVar4 = (longlong *)*param_3;
  local_218 = auVar133._0_4_;
  local_208 = lVar13;
  lVar14 = (**(code **)(*plVar4 + 0xd8))(plVar4,*(undefined4 *)(lVar14 + 0x1e0));
  iVar3 = *(int *)(param_3[1] + 0x208);
  fVar134 = (float)*(int *)(param_3[1] + 0x2c4) / _DAT_14046679c;
  fVar135 = *(float *)((longlong)plVar4 + 0x2275c);
  auVar101._16_48_ = in_ZMM3._16_48_;
  auVar101._0_16_ = ZEXT416((uint)fVar135);
  auVar102._4_60_ = auVar101._4_60_;
  if (fVar135 == DAT_1404debbc) {
    auVar101._0_16_ = ZEXT416((uint)DAT_1404debbc);
  }
  else if (*(int *)((longlong)plVar4 + 0x1ac) < iVar3) {
    fVar72 = (float)(100 - iVar3) / (float)(100 - *(int *)((longlong)plVar4 + 0x1ac));
    fVar56 = DAT_1404debbc - fVar72;
    auVar102._0_4_ = fVar135 * fVar72 + fVar56 + fVar56;
    auVar101 = auVar102;
  }
  fVar135 = (((float)(iVar3 + 100) * DAT_1404deb28 * *(float *)(lVar14 + 0x1060) * fVar134) /
            DAT_140468bf0) * auVar101._0_4_;
  if (fVar134 <= DAT_1404debbc) {
    fVar134 = (fVar134 - DAT_1404debbc) * DAT_140468be8 + auVar108._0_4_;
  }
  else {
    fVar134 = fVar134 * auVar108._0_4_;
  }
  local_258 = 2;
  (*(code *)local_120[0xb])(&local_120,2,2,2);
  uVar25 = (local_110 - local_118) - 6;
  uVar23 = uVar25 >> 2;
  local_228 = operator_new(0x18);
  pauVar22 = (undefined1 (*) [32])0x0;
  if (local_228 == (uint *)0x0) {
    local_228 = (uint *)0x0;
  }
  else {
    local_228[0] = 0;
    local_228[1] = 0;
    local_228[2] = 0;
    local_228[3] = 0;
    local_228[0] = 0;
    local_228[1] = 0;
    local_228[2] = 0;
    local_228[3] = 0;
    local_228[4] = 0;
    local_228[5] = 0;
  }
  puVar40 = local_228;
  uVar41 = iVar2 + 6;
  iVar42 = iVar42 + 6;
  local_210 = (undefined1 (*) [32])CONCAT44(local_210._4_4_,iVar42);
  local_240 = uVar41;
  FUN_1401869f0(local_228,iVar42,uVar41);
  auVar129 = in_ZMM12._4_60_;
  auVar124 = in_ZMM11._4_60_;
  auVar121 = in_ZMM10._4_60_;
  if (*(void **)(puVar40 + 4) != (void *)0x0) {
    memset(*(void **)(puVar40 + 4),0,(ulonglong)puVar40[3]);
    auVar129 = in_ZMM12._4_60_;
    auVar124 = in_ZMM11._4_60_;
    auVar121 = in_ZMM10._4_60_;
  }
  auVar119 = auVar133._0_16_;
  auVar120._16_48_ = auVar121._12_48_;
  auVar120._0_16_ = ZEXT416(DAT_1404df118);
  auVar123._16_48_ = auVar124._12_48_;
  auVar123._0_16_ = ZEXT416(DAT_140468bec);
  auVar128._16_48_ = auVar129._12_48_;
  auVar128._0_16_ = ZEXT416(DAT_1404df1d0);
  if (local_114 < local_10c) {
    uVar12 = uVar23 * 4;
    uVar36 = local_114;
    do {
      auVar131 = auVar133._0_16_;
      auVar127 = auVar128._0_16_;
      auVar122 = auVar123._0_16_;
      auVar119 = auVar120._0_16_;
      pauVar19 = (undefined1 (*) [16])
                 ((ulonglong)(uVar36 * *(int *)(lVar13 + 0x14)) + (ulonglong)local_118 * 2 +
                 *(longlong *)(lVar13 + 0x20));
      pauVar28 = pauVar22;
      if ((*(longlong *)(puVar40 + 4) != 0) && (uVar36 + 3 < puVar40[1])) {
        if (*puVar40 < 4) {
          pauVar28 = (undefined1 (*) [32])0x0;
        }
        else {
          pauVar28 = (undefined1 (*) [32])
                     ((ulonglong)((uVar36 + 3) * puVar40[2]) + 0xc + *(longlong *)(puVar40 + 4));
        }
      }
      *(float *)(pauVar28[-1] + 0x14) = (float)*(ushort *)*pauVar19;
      fVar56 = auVar120._0_4_;
      *(float *)(pauVar28[-1] + 0x18) =
           (float)*(ushort *)*pauVar19 * fVar56 + (float)*(ushort *)(*pauVar19 + 2);
      fVar72 = auVar123._0_4_;
      *(float *)(pauVar28[-1] + 0x1c) =
           (float)*(ushort *)(*pauVar19 + 2) * fVar56 + (float)*(ushort *)(*pauVar19 + 4) +
           (float)*(ushort *)*pauVar19 * fVar72;
      fVar125 = auVar128._0_4_;
      *(float *)*pauVar28 =
           (float)*(ushort *)(*pauVar19 + 4) * fVar56 + (float)*(ushort *)(*pauVar19 + 6) +
           (float)*(ushort *)(*pauVar19 + 2) * fVar72 + (float)*(ushort *)*pauVar19 * fVar125;
      *(float *)((longlong)*pauVar28 + 4) =
           (float)*(ushort *)(*pauVar19 + 6) * fVar56 + (float)*(ushort *)(*pauVar19 + 8) +
           (float)*(ushort *)(*pauVar19 + 4) * fVar72 + (float)*(ushort *)(*pauVar19 + 2) * fVar125
           + (float)*(ushort *)*pauVar19 * fVar72;
      *(float *)((longlong)*pauVar28 + 8) =
           (float)*(ushort *)(*pauVar19 + 10) + (float)*(ushort *)(*pauVar19 + 8) * fVar56 +
           (float)*(ushort *)(*pauVar19 + 6) * fVar72 + (float)*(ushort *)(*pauVar19 + 4) * fVar125
           + (float)*(ushort *)(*pauVar19 + 2) * fVar72 + (float)*(ushort *)*pauVar19 * fVar56;
      auVar61 = _DAT_140468c00;
      pfVar27 = (float *)((longlong)*pauVar28 + 0xc);
      if (uVar25 >> 2 != 0) {
        uVar15 = (ulonglong)uVar23;
        do {
          auVar6 = vpunpckhwd_avx(*pauVar19,ZEXT816(0));
          auVar7 = vpunpcklwd_avx(*pauVar19,ZEXT816(0));
          auVar57._16_16_ = auVar6;
          auVar57._0_16_ = auVar7;
          auVar57 = vcvtdq2ps_avx(auVar57);
          fVar126 = auVar61._0_4_;
          auVar110._0_4_ = auVar57._0_4_ * fVar126;
          fVar130 = auVar61._4_4_;
          auVar110._4_4_ = auVar57._4_4_ * fVar130;
          fVar8 = auVar61._8_4_;
          auVar110._8_4_ = auVar57._8_4_ * fVar8;
          fVar73 = auVar61._12_4_;
          auVar110._12_4_ = auVar57._12_4_ * fVar73;
          fVar103 = auVar61._16_4_;
          auVar110._16_4_ = auVar57._16_4_ * fVar103;
          fVar5 = auVar61._20_4_;
          auVar110._20_4_ = auVar57._20_4_ * fVar5;
          fVar112 = auVar61._24_4_;
          auVar110._24_4_ = auVar57._24_4_ * fVar112;
          fVar113 = auVar61._28_4_;
          auVar110._28_4_ = auVar57._28_4_ * fVar113;
          auVar6 = vpunpckhwd_avx(*(undefined1 (*) [16])(*pauVar19 + 2),ZEXT816(0));
          auVar7 = vpunpcklwd_avx(*(undefined1 (*) [16])(*pauVar19 + 2),ZEXT816(0));
          auVar58._16_16_ = auVar6;
          auVar58._0_16_ = auVar7;
          auVar57 = vcvtdq2ps_avx(auVar58);
          auVar106._0_4_ = auVar57._0_4_ * fVar126;
          auVar106._4_4_ = auVar57._4_4_ * fVar130;
          auVar106._8_4_ = auVar57._8_4_ * fVar8;
          auVar106._12_4_ = auVar57._12_4_ * fVar73;
          auVar106._16_4_ = auVar57._16_4_ * fVar103;
          auVar106._20_4_ = auVar57._20_4_ * fVar5;
          auVar106._24_4_ = auVar57._24_4_ * fVar112;
          auVar106._28_4_ = auVar57._28_4_ * fVar113;
          auVar6 = vpunpckhwd_avx(*(undefined1 (*) [16])(*pauVar19 + 6),ZEXT816(0));
          auVar7 = vpunpcklwd_avx(*(undefined1 (*) [16])(*pauVar19 + 6),ZEXT816(0));
          auVar59._16_16_ = auVar6;
          auVar59._0_16_ = auVar7;
          auVar57 = vcvtdq2ps_avx(auVar59);
          auVar104._0_4_ = auVar57._0_4_ * fVar126;
          auVar104._4_4_ = auVar57._4_4_ * fVar130;
          auVar104._8_4_ = auVar57._8_4_ * fVar8;
          auVar104._12_4_ = auVar57._12_4_ * fVar73;
          auVar104._16_4_ = auVar57._16_4_ * fVar103;
          auVar104._20_4_ = auVar57._20_4_ * fVar5;
          auVar104._24_4_ = auVar57._24_4_ * fVar112;
          auVar104._28_4_ = auVar57._28_4_ * fVar113;
          auVar6 = vpunpckhwd_avx(*(undefined1 (*) [16])(*pauVar19 + 4),ZEXT816(0));
          auVar7 = vpunpcklwd_avx(*(undefined1 (*) [16])(*pauVar19 + 4),ZEXT816(0));
          auVar60._16_16_ = auVar6;
          auVar60._0_16_ = auVar7;
          auVar57 = vcvtdq2ps_avx(auVar60);
          auVar98._0_4_ = auVar57._0_4_ * fVar126;
          auVar98._4_4_ = auVar57._4_4_ * fVar130;
          auVar98._8_4_ = auVar57._8_4_ * fVar8;
          auVar98._12_4_ = auVar57._12_4_ * fVar73;
          auVar98._16_4_ = auVar57._16_4_ * fVar103;
          auVar98._20_4_ = auVar57._20_4_ * fVar5;
          auVar98._24_4_ = auVar57._24_4_ * fVar112;
          auVar98._28_4_ = auVar57._28_4_ * fVar113;
          auVar57 = vhaddps_avx(auVar98,auVar104);
          auVar58 = vhaddps_avx(auVar110,auVar106);
          auVar57 = vhaddps_avx(auVar58,auVar57);
          *pfVar27 = auVar57._16_4_ + auVar57._0_4_;
          pfVar27[1] = auVar57._20_4_ + auVar57._4_4_;
          pfVar27[2] = auVar57._24_4_ + auVar57._8_4_;
          pfVar27[3] = auVar57._28_4_ + auVar57._12_4_;
          pfVar27 = pfVar27 + 4;
          pauVar19 = (undefined1 (*) [16])(*pauVar19 + 8);
          uVar15 = uVar15 - 1;
        } while (uVar15 != 0);
      }
      if (uVar12 < uVar25) {
        uVar41 = uVar12;
        if (3 < uVar25 + uVar23 * -4) {
          uVar41 = ((uVar25 + uVar23 * -4) - 4 >> 2) + 1;
          uVar15 = (ulonglong)uVar41;
          uVar41 = uVar12 + uVar41 * 4;
          auVar120 = ZEXT1664(auVar119);
          auVar123 = ZEXT1664(auVar122);
          auVar128 = ZEXT1664(auVar127);
          auVar133 = ZEXT1664(auVar131);
          do {
            *pfVar27 = (float)*(ushort *)(*pauVar19 + 10) * fVar56 +
                       (float)*(ushort *)(*pauVar19 + 0xc) +
                       (float)*(ushort *)(*pauVar19 + 8) * fVar72 +
                       (float)*(ushort *)(*pauVar19 + 6) * fVar125 +
                       (float)*(ushort *)(*pauVar19 + 4) * fVar72 +
                       (float)*(ushort *)(*pauVar19 + 2) * fVar56 + (float)*(ushort *)*pauVar19;
            pfVar27[1] = (float)*(ushort *)(*pauVar19 + 0xc) * fVar56 +
                         (float)*(ushort *)(*pauVar19 + 0xe) +
                         (float)*(ushort *)(*pauVar19 + 10) * fVar72 +
                         (float)*(ushort *)(*pauVar19 + 8) * fVar125 +
                         (float)*(ushort *)(*pauVar19 + 6) * fVar72 +
                         (float)*(ushort *)(*pauVar19 + 4) * fVar56 +
                         (float)*(ushort *)(*pauVar19 + 2);
            pfVar27[2] = (float)*(ushort *)(*pauVar19 + 0xe) * fVar56 +
                         (float)*(ushort *)pauVar19[1] +
                         (float)*(ushort *)(*pauVar19 + 0xc) * fVar72 +
                         (float)*(ushort *)(*pauVar19 + 10) * fVar125 +
                         (float)*(ushort *)(*pauVar19 + 8) * fVar72 +
                         (float)*(ushort *)(*pauVar19 + 6) * fVar56 +
                         (float)*(ushort *)(*pauVar19 + 4);
            pfVar27[3] = (float)*(ushort *)pauVar19[1] * fVar56 +
                         (float)*(ushort *)(pauVar19[1] + 2) +
                         (float)*(ushort *)(*pauVar19 + 0xe) * fVar72 +
                         (float)*(ushort *)(*pauVar19 + 0xc) * fVar125 +
                         (float)*(ushort *)(*pauVar19 + 10) * fVar72 +
                         (float)*(ushort *)(*pauVar19 + 8) * fVar56 +
                         (float)*(ushort *)(*pauVar19 + 6);
            pfVar27 = pfVar27 + 4;
            pauVar19 = (undefined1 (*) [16])(*pauVar19 + 8);
            uVar15 = uVar15 - 1;
          } while (uVar15 != 0);
          if (uVar25 <= uVar41) goto LAB_140387678;
        }
        uVar15 = (ulonglong)(uVar25 - uVar41);
        auVar120 = ZEXT1664(auVar119);
        auVar123 = ZEXT1664(auVar122);
        auVar128 = ZEXT1664(auVar127);
        auVar133 = ZEXT1664(auVar131);
        do {
          *pfVar27 = (float)*(ushort *)(*pauVar19 + 10) * fVar56 +
                     (float)*(ushort *)(*pauVar19 + 0xc) +
                     (float)*(ushort *)(*pauVar19 + 8) * fVar72 +
                     (float)*(ushort *)(*pauVar19 + 6) * fVar125 +
                     (float)*(ushort *)(*pauVar19 + 4) * fVar72 +
                     (float)*(ushort *)(*pauVar19 + 2) * fVar56 + (float)*(ushort *)*pauVar19;
          pfVar27 = pfVar27 + 1;
          pauVar19 = (undefined1 (*) [16])(*pauVar19 + 2);
          uVar15 = uVar15 - 1;
        } while (uVar15 != 0);
      }
LAB_140387678:
      auVar119 = auVar133._0_16_;
      fVar56 = auVar120._0_4_;
      fVar72 = auVar123._0_4_;
      fVar125 = auVar128._0_4_;
      *pfVar27 = (float)*(ushort *)(*pauVar19 + 10) * fVar56 +
                 (float)*(ushort *)(*pauVar19 + 8) * fVar72 +
                 (float)*(ushort *)(*pauVar19 + 6) * fVar125 +
                 (float)*(ushort *)(*pauVar19 + 4) * fVar72 +
                 (float)*(ushort *)(*pauVar19 + 2) * fVar56 + (float)*(ushort *)*pauVar19;
      pfVar27[1] = (float)*(ushort *)(*pauVar19 + 4) * fVar56 +
                   (float)*(ushort *)(*pauVar19 + 10) * fVar72 +
                   (float)*(ushort *)(*pauVar19 + 8) * fVar125 +
                   (float)*(ushort *)(*pauVar19 + 6) * fVar72 + (float)*(ushort *)(*pauVar19 + 2);
      pfVar27[2] = (float)*(ushort *)(*pauVar19 + 6) * fVar56 +
                   (float)*(ushort *)(*pauVar19 + 10) * fVar125 +
                   (float)*(ushort *)(*pauVar19 + 8) * fVar72 + (float)*(ushort *)(*pauVar19 + 4);
      pfVar27[3] = (float)*(ushort *)(*pauVar19 + 8) * fVar56 +
                   (float)*(ushort *)(*pauVar19 + 10) * fVar72 + (float)*(ushort *)(*pauVar19 + 6);
      pfVar27[4] = (float)*(ushort *)(*pauVar19 + 10) * fVar56 + (float)*(ushort *)(*pauVar19 + 8);
      pfVar27[5] = (float)*(ushort *)(*pauVar19 + 10);
      uVar36 = uVar36 + 1;
      uVar41 = local_240;
    } while (uVar36 < local_10c);
  }
  uVar23 = local_110 - local_118;
  uVar25 = uVar23 >> 3;
  auVar109 = ZEXT1664(auVar120._0_16_);
  auVar108 = ZEXT1664(auVar123._0_16_);
  auVar55 = ZEXT1664(auVar128._0_16_);
  auVar120 = ZEXT1664(auVar119);
  local_23c = uVar25;
  local_234 = uVar23;
  local_220 = operator_new(0x18);
  if (local_220 == (uint *)0x0) {
    local_220 = (uint *)0x0;
  }
  else {
    local_220[0] = 0;
    local_220[1] = 0;
    local_220[2] = 0;
    local_220[3] = 0;
    local_220[0] = 0;
    local_220[1] = 0;
    local_220[2] = 0;
    local_220[3] = 0;
    local_220[4] = 0;
    local_220[5] = 0;
  }
  puVar40 = local_220;
  FUN_1401869f0(local_220,iVar42,uVar41);
  fVar56 = DAT_1404defe0;
  auVar119 = auVar120._0_16_;
  auVar128._0_16_ = auVar55._0_16_;
  auVar123._0_16_ = auVar108._0_16_;
  auVar120._0_16_ = auVar109._0_16_;
  if (local_114 < local_10c) {
    uVar41 = uVar25 * 8;
    uVar12 = uVar25;
    uVar36 = local_114;
    do {
      auVar128._0_16_ = auVar55._0_16_;
      auVar123._0_16_ = auVar108._0_16_;
      auVar120._0_16_ = auVar109._0_16_;
      lVar14 = *(longlong *)(local_208 + 0x20);
      iVar2 = *(int *)(local_208 + 0x14);
      lVar13 = (ulonglong)local_118 * 2;
      pauVar33 = (undefined1 (*) [16])((ulonglong)((uVar36 - 1) * iVar2) + lVar14 + lVar13);
      pauVar19 = (undefined1 (*) [16])((ulonglong)(iVar2 * uVar36) + lVar14 + lVar13);
      pauVar37 = (undefined1 (*) [16])((ulonglong)((uVar36 + 1) * iVar2) + lVar14 + lVar13);
      pauVar28 = pauVar22;
      if ((*(longlong *)(puVar40 + 4) != 0) && (uVar36 + 3 < puVar40[1])) {
        if (*puVar40 < 4) {
          pauVar28 = (undefined1 (*) [32])0x0;
        }
        else {
          pauVar28 = (undefined1 (*) [32])
                     ((ulonglong)((uVar36 + 3) * puVar40[2]) + 0xc + *(longlong *)(puVar40 + 4));
        }
      }
      if (uVar12 != 0) {
        uVar15 = (ulonglong)uVar12;
        do {
          auVar119 = vpunpckhwd_avx(*pauVar19,ZEXT816(0));
          auVar122 = vpunpcklwd_avx(*pauVar19,ZEXT816(0));
          auVar61._16_16_ = auVar119;
          auVar61._0_16_ = auVar122;
          auVar61 = vcvtdq2ps_avx(auVar61);
          auVar119 = vpunpckhwd_avx(*(undefined1 (*) [16])(*pauVar19 + 2),ZEXT816(0));
          auVar122 = vpunpcklwd_avx(*(undefined1 (*) [16])(*pauVar19 + 2),ZEXT816(0));
          auVar62._16_16_ = auVar119;
          auVar62._0_16_ = auVar122;
          auVar57 = vcvtdq2ps_avx(auVar62);
          auVar119 = vpunpckhwd_avx(*(undefined1 (*) [16])(pauVar19[-1] + 0xe),ZEXT816(0));
          auVar122 = vpunpcklwd_avx(*(undefined1 (*) [16])(pauVar19[-1] + 0xe),ZEXT816(0));
          auVar63._16_16_ = auVar119;
          auVar63._0_16_ = auVar122;
          auVar58 = vcvtdq2ps_avx(auVar63);
          auVar119 = vpunpckhwd_avx(*pauVar37,ZEXT816(0));
          auVar122 = vpunpcklwd_avx(*pauVar37,ZEXT816(0));
          auVar64._16_16_ = auVar119;
          auVar64._0_16_ = auVar122;
          auVar59 = vcvtdq2ps_avx(auVar64);
          auVar119 = vpunpckhwd_avx(*pauVar33,ZEXT816(0));
          auVar122 = vpunpcklwd_avx(*pauVar33,ZEXT816(0));
          auVar65._16_16_ = auVar119;
          auVar65._0_16_ = auVar122;
          auVar60 = vcvtdq2ps_avx(auVar65);
          auVar99._0_4_ =
               auVar61._0_4_ * _DAT_140468c20 + auVar57._0_4_ + auVar58._0_4_ + auVar59._0_4_ +
               auVar60._0_4_;
          auVar99._4_4_ =
               auVar61._4_4_ * _UNK_140468c24 + auVar57._4_4_ + auVar58._4_4_ + auVar59._4_4_ +
               auVar60._4_4_;
          auVar99._8_4_ =
               auVar61._8_4_ * _UNK_140468c28 + auVar57._8_4_ + auVar58._8_4_ + auVar59._8_4_ +
               auVar60._8_4_;
          auVar99._12_4_ =
               auVar61._12_4_ * _UNK_140468c2c + auVar57._12_4_ + auVar58._12_4_ + auVar59._12_4_ +
               auVar60._12_4_;
          auVar99._16_4_ =
               auVar61._16_4_ * _UNK_140468c30 + auVar57._16_4_ + auVar58._16_4_ + auVar59._16_4_ +
               auVar60._16_4_;
          auVar99._20_4_ =
               auVar61._20_4_ * _UNK_140468c34 + auVar57._20_4_ + auVar58._20_4_ + auVar59._20_4_ +
               auVar60._20_4_;
          auVar99._24_4_ =
               auVar61._24_4_ * _UNK_140468c38 + auVar57._24_4_ + auVar58._24_4_ + auVar59._24_4_ +
               auVar60._24_4_;
          auVar99._28_4_ =
               auVar61._28_4_ * _UNK_140468c3c + auVar57._28_4_ + auVar58._28_4_ + auVar59._28_4_ +
               auVar60._28_4_;
          *pauVar28 = auVar99;
          pauVar28 = pauVar28 + 1;
          pauVar19 = pauVar19 + 1;
          pauVar33 = pauVar33 + 1;
          pauVar37 = pauVar37 + 1;
          uVar15 = uVar15 - 1;
        } while (uVar15 != 0);
      }
      if (uVar41 < uVar23) {
        uVar12 = uVar41;
        if (3 < uVar23 + uVar25 * -8) {
          uVar12 = ((uVar23 + uVar25 * -8) - 4 >> 2) + 1;
          uVar15 = (ulonglong)uVar12;
          uVar12 = uVar41 + uVar12 * 4;
          auVar109 = ZEXT1664(auVar120._0_16_);
          auVar108 = ZEXT1664(auVar123._0_16_);
          auVar55 = ZEXT1664(auVar128._0_16_);
          do {
            *(float *)*pauVar28 =
                 (float)*(ushort *)*pauVar37 + (float)*(ushort *)*pauVar33 +
                 (float)*(ushort *)(pauVar19[-1] + 0xe) + (float)*(ushort *)(*pauVar19 + 2) +
                 (float)*(ushort *)*pauVar19 * fVar56;
            puVar1 = (ushort *)(*pauVar19 + 4);
            *(float *)((longlong)*pauVar28 + 4) =
                 (float)*(ushort *)(*pauVar37 + 2) + (float)*(ushort *)(*pauVar33 + 2) +
                 (float)*(ushort *)*pauVar19 + (float)*puVar1 +
                 (float)*(ushort *)(*pauVar19 + 2) * fVar56;
            puVar9 = *pauVar19;
            *(float *)((longlong)*pauVar28 + 8) =
                 (float)*(ushort *)(*pauVar37 + 4) + (float)*(ushort *)(*pauVar33 + 4) +
                 (float)*(ushort *)(*pauVar19 + 2) + (float)*(ushort *)(puVar9 + 6) +
                 (float)*puVar1 * fVar56;
            pauVar19 = (undefined1 (*) [16])(*pauVar19 + 8);
            *(float *)((longlong)*pauVar28 + 0xc) =
                 (float)*(ushort *)(*pauVar37 + 6) + (float)*(ushort *)(*pauVar33 + 6) +
                 (float)*puVar1 + (float)*(ushort *)*pauVar19 +
                 (float)*(ushort *)(puVar9 + 6) * fVar56;
            pauVar28 = (undefined1 (*) [32])((longlong)*pauVar28 + 0x10);
            pauVar37 = (undefined1 (*) [16])(*pauVar37 + 8);
            pauVar33 = (undefined1 (*) [16])(*pauVar33 + 8);
            uVar15 = uVar15 - 1;
          } while (uVar15 != 0);
          if (uVar23 <= uVar12) goto LAB_140387c28;
        }
        uVar15 = (ulonglong)(uVar23 - uVar12);
        auVar109 = ZEXT1664(auVar120._0_16_);
        auVar108 = ZEXT1664(auVar123._0_16_);
        auVar55 = ZEXT1664(auVar128._0_16_);
        do {
          *(float *)*pauVar28 =
               (float)*(ushort *)*pauVar33 + (float)*(ushort *)*pauVar37 +
               (float)*(ushort *)(pauVar19[-1] + 0xe) +
               (float)*(ushort *)*(undefined1 (*) [16])(*pauVar19 + 2) +
               (float)*(ushort *)*pauVar19 * fVar56;
          pauVar28 = (undefined1 (*) [32])((longlong)*pauVar28 + 4);
          pauVar37 = (undefined1 (*) [16])(*pauVar37 + 2);
          pauVar33 = (undefined1 (*) [16])(*pauVar33 + 2);
          uVar15 = uVar15 - 1;
          pauVar19 = (undefined1 (*) [16])(*pauVar19 + 2);
        } while (uVar15 != 0);
      }
LAB_140387c28:
      auVar128._0_16_ = auVar55._0_16_;
      auVar123._0_16_ = auVar108._0_16_;
      auVar120._0_16_ = auVar109._0_16_;
      uVar36 = uVar36 + 1;
      uVar12 = local_23c;
    } while (uVar36 < local_10c);
    auVar119 = ZEXT416((uint)local_218);
    iVar42 = (int)local_210;
    uVar25 = local_23c;
  }
  auVar109 = ZEXT1664(auVar120._0_16_);
  auVar108 = ZEXT1664(auVar123._0_16_);
  auVar55 = ZEXT1664(auVar128._0_16_);
  auVar101 = ZEXT1664(auVar119);
  local_210 = operator_new(0x18);
  pauVar28 = pauVar22;
  if (local_210 != (undefined1 (*) [32])0x0) {
    *(undefined8 *)*local_210 = 0;
    *(undefined8 *)((longlong)*local_210 + 8) = 0;
    *(undefined8 *)*local_210 = 0;
    *(undefined8 *)((longlong)*local_210 + 8) = 0;
    *(undefined8 *)((longlong)*local_210 + 0x10) = 0;
    pauVar28 = local_210;
  }
  FUN_1401869f0(pauVar28,iVar42,local_240);
  _Dst = *(void **)((longlong)*pauVar28 + 0x10);
  if (_Dst != (void *)0x0) {
    memset(_Dst,0,(ulonglong)*(uint *)((longlong)*pauVar28 + 0xc));
  }
  fVar56 = local_108;
  if (local_114 < local_10c) {
    local_240 = uVar25 * 8;
    uVar41 = local_114 + 2;
    do {
      auVar119 = auVar101._0_16_;
      auVar128._0_16_ = auVar55._0_16_;
      auVar123._0_16_ = auVar108._0_16_;
      auVar120._0_16_ = auVar109._0_16_;
      lVar14 = *(longlong *)((longlong)*pauVar28 + 0x10);
      pauVar17 = pauVar22;
      pauVar20 = pauVar22;
      pauVar18 = pauVar22;
      pauVar30 = pauVar22;
      pauVar31 = pauVar22;
      pauVar34 = pauVar22;
      pauVar38 = pauVar22;
      if (lVar14 != 0) {
        if ((uVar41 - 2 < *(uint *)((longlong)*pauVar28 + 4)) && (3 < *(uint *)*pauVar28)) {
          pauVar18 = (undefined1 (*) [32])
                     ((ulonglong)((uVar41 - 2) * *(int *)((longlong)*pauVar28 + 8)) + 0xc + lVar14);
        }
        if ((uVar41 - 1 < *(uint *)((longlong)*pauVar28 + 4)) && (3 < *(uint *)*pauVar28)) {
          pauVar38 = (undefined1 (*) [32])
                     ((ulonglong)((uVar41 - 1) * *(int *)((longlong)*pauVar28 + 8)) + 0xc + lVar14);
        }
        if ((uVar41 < *(uint *)((longlong)*pauVar28 + 4)) && (3 < *(uint *)*pauVar28)) {
          pauVar34 = (undefined1 (*) [32])
                     ((ulonglong)(uVar41 * *(int *)((longlong)*pauVar28 + 8)) + 0xc + lVar14);
        }
        if ((uVar41 + 1 < *(uint *)((longlong)*pauVar28 + 4)) && (3 < *(uint *)*pauVar28)) {
          pauVar31 = (undefined1 (*) [32])
                     ((ulonglong)((uVar41 + 1) * *(int *)((longlong)*pauVar28 + 8)) + 0xc + lVar14);
        }
        if ((uVar41 + 2 < *(uint *)((longlong)*pauVar28 + 4)) && (3 < *(uint *)*pauVar28)) {
          pauVar30 = (undefined1 (*) [32])
                     ((ulonglong)((uVar41 + 2) * *(int *)((longlong)*pauVar28 + 8)) + 0xc + lVar14);
        }
        if ((uVar41 + 3 < *(uint *)((longlong)*pauVar28 + 4)) && (3 < *(uint *)*pauVar28)) {
          pauVar20 = (undefined1 (*) [32])
                     ((ulonglong)((uVar41 + 3) * *(int *)((longlong)*pauVar28 + 8)) + 0xc + lVar14);
        }
        if (uVar41 + 4 < *(uint *)((longlong)*pauVar28 + 4)) {
          if (*(uint *)*pauVar28 < 4) {
            pauVar17 = (undefined1 (*) [32])0x0;
          }
          else {
            pauVar17 = (undefined1 (*) [32])
                       (lVar14 + 0xc + (ulonglong)((uVar41 + 4) * *(int *)((longlong)*pauVar28 + 8))
                       );
          }
        }
      }
      pauVar24 = pauVar22;
      if ((*(longlong *)(local_228 + 4) != 0) && (uVar23 = local_234, uVar41 + 1 < local_228[1])) {
        if (*local_228 < 4) {
          pauVar24 = (undefined1 (*) [32])0x0;
        }
        else {
          pauVar24 = (undefined1 (*) [32])
                     (*(longlong *)(local_228 + 4) + 0xc + (ulonglong)((uVar41 + 1) * local_228[2]))
          ;
        }
      }
      uVar15 = (ulonglong)local_23c;
      if (local_23c != 0) {
        do {
          fVar72 = *(float *)*pauVar24;
          fVar125 = *(float *)((longlong)*pauVar24 + 4);
          fVar126 = *(float *)((longlong)*pauVar24 + 8);
          fVar130 = *(float *)((longlong)*pauVar24 + 0xc);
          fVar8 = *(float *)((longlong)*pauVar24 + 0x10);
          fVar73 = *(float *)((longlong)*pauVar24 + 0x14);
          fVar103 = *(float *)((longlong)*pauVar24 + 0x18);
          fVar5 = *(float *)((longlong)*pauVar24 + 0x1c);
          fVar112 = fVar125 * _UNK_140467884;
          fVar113 = fVar126 * _UNK_140467888;
          fVar114 = fVar130 * _UNK_14046788c;
          fVar115 = fVar8 * _UNK_140467890;
          fVar116 = fVar73 * _UNK_140467894;
          fVar117 = fVar103 * _UNK_140467898;
          fVar118 = fVar5 * _UNK_14046789c;
          auVar111._0_4_ = fVar72 * _DAT_140468c40 + *(float *)*pauVar38;
          auVar111._4_4_ = fVar125 * _UNK_140468c44 + *(float *)((longlong)*pauVar38 + 4);
          auVar111._8_4_ = fVar126 * _UNK_140468c48 + *(float *)((longlong)*pauVar38 + 8);
          auVar111._12_4_ = fVar130 * _UNK_140468c4c + *(float *)((longlong)*pauVar38 + 0xc);
          auVar111._16_4_ = fVar8 * _UNK_140468c50 + *(float *)((longlong)*pauVar38 + 0x10);
          auVar111._20_4_ = fVar73 * _UNK_140468c54 + *(float *)((longlong)*pauVar38 + 0x14);
          auVar111._24_4_ = fVar103 * _UNK_140468c58 + *(float *)((longlong)*pauVar38 + 0x18);
          auVar111._28_4_ = fVar5 * _UNK_140468c5c + *(float *)((longlong)*pauVar38 + 0x1c);
          auVar107._0_4_ = fVar72 * _DAT_140468c60 + *(float *)*pauVar34;
          auVar107._4_4_ = fVar125 * _UNK_140468c64 + *(float *)((longlong)*pauVar34 + 4);
          auVar107._8_4_ = fVar126 * _UNK_140468c68 + *(float *)((longlong)*pauVar34 + 8);
          auVar107._12_4_ = fVar130 * _UNK_140468c6c + *(float *)((longlong)*pauVar34 + 0xc);
          auVar107._16_4_ = fVar8 * _UNK_140468c70 + *(float *)((longlong)*pauVar34 + 0x10);
          auVar107._20_4_ = fVar73 * _UNK_140468c74 + *(float *)((longlong)*pauVar34 + 0x14);
          auVar107._24_4_ = fVar103 * _UNK_140468c78 + *(float *)((longlong)*pauVar34 + 0x18);
          auVar107._28_4_ = fVar5 * _UNK_140468c7c + *(float *)((longlong)*pauVar34 + 0x1c);
          auVar105._0_4_ = fVar72 * _DAT_140468c80 + *(float *)*pauVar31;
          auVar105._4_4_ = fVar125 * _UNK_140468c84 + *(float *)((longlong)*pauVar31 + 4);
          auVar105._8_4_ = fVar126 * _UNK_140468c88 + *(float *)((longlong)*pauVar31 + 8);
          auVar105._12_4_ = fVar130 * _UNK_140468c8c + *(float *)((longlong)*pauVar31 + 0xc);
          auVar105._16_4_ = fVar8 * _UNK_140468c90 + *(float *)((longlong)*pauVar31 + 0x10);
          auVar105._20_4_ = fVar73 * _UNK_140468c94 + *(float *)((longlong)*pauVar31 + 0x14);
          auVar105._24_4_ = fVar103 * _UNK_140468c98 + *(float *)((longlong)*pauVar31 + 0x18);
          auVar105._28_4_ = fVar5 * _UNK_140468c9c + *(float *)((longlong)*pauVar31 + 0x1c);
          auVar100._0_4_ = fVar72 * _DAT_140468c60 + *(float *)*pauVar30;
          auVar100._4_4_ = fVar125 * _UNK_140468c64 + *(float *)((longlong)*pauVar30 + 4);
          auVar100._8_4_ = fVar126 * _UNK_140468c68 + *(float *)((longlong)*pauVar30 + 8);
          auVar100._12_4_ = fVar130 * _UNK_140468c6c + *(float *)((longlong)*pauVar30 + 0xc);
          auVar100._16_4_ = fVar8 * _UNK_140468c70 + *(float *)((longlong)*pauVar30 + 0x10);
          auVar100._20_4_ = fVar73 * _UNK_140468c74 + *(float *)((longlong)*pauVar30 + 0x14);
          auVar100._24_4_ = fVar103 * _UNK_140468c78 + *(float *)((longlong)*pauVar30 + 0x18);
          auVar100._28_4_ = fVar5 * _UNK_140468c7c + *(float *)((longlong)*pauVar30 + 0x1c);
          auVar74._0_4_ = fVar72 * _DAT_140468c40 + *(float *)*pauVar20;
          auVar74._4_4_ = fVar125 * _UNK_140468c44 + *(float *)((longlong)*pauVar20 + 4);
          auVar74._8_4_ = fVar126 * _UNK_140468c48 + *(float *)((longlong)*pauVar20 + 8);
          auVar74._12_4_ = fVar130 * _UNK_140468c4c + *(float *)((longlong)*pauVar20 + 0xc);
          auVar74._16_4_ = fVar8 * _UNK_140468c50 + *(float *)((longlong)*pauVar20 + 0x10);
          auVar74._20_4_ = fVar73 * _UNK_140468c54 + *(float *)((longlong)*pauVar20 + 0x14);
          auVar74._24_4_ = fVar103 * _UNK_140468c58 + *(float *)((longlong)*pauVar20 + 0x18);
          auVar74._28_4_ = fVar5 * _UNK_140468c5c + *(float *)((longlong)*pauVar20 + 0x1c);
          auVar66._0_4_ = fVar72 * _DAT_140467880 + *(float *)*pauVar17;
          auVar66._4_4_ = fVar112 + *(float *)((longlong)*pauVar17 + 4);
          auVar66._8_4_ = fVar113 + *(float *)((longlong)*pauVar17 + 8);
          auVar66._12_4_ = fVar114 + *(float *)((longlong)*pauVar17 + 0xc);
          auVar66._16_4_ = fVar115 + *(float *)((longlong)*pauVar17 + 0x10);
          auVar66._20_4_ = fVar116 + *(float *)((longlong)*pauVar17 + 0x14);
          auVar66._24_4_ = fVar117 + *(float *)((longlong)*pauVar17 + 0x18);
          auVar66._28_4_ = fVar118 + *(float *)((longlong)*pauVar17 + 0x1c);
          fVar125 = *(float *)((longlong)*pauVar18 + 4);
          fVar126 = *(float *)((longlong)*pauVar18 + 8);
          fVar130 = *(float *)((longlong)*pauVar18 + 0xc);
          fVar8 = *(float *)((longlong)*pauVar18 + 0x10);
          fVar73 = *(float *)((longlong)*pauVar18 + 0x14);
          fVar103 = *(float *)((longlong)*pauVar18 + 0x18);
          fVar5 = *(float *)((longlong)*pauVar18 + 0x1c);
          *(float *)*pauVar18 = fVar72 * _DAT_140467880 + *(float *)*pauVar18;
          *(float *)((longlong)*pauVar18 + 4) = fVar112 + fVar125;
          *(float *)((longlong)*pauVar18 + 8) = fVar113 + fVar126;
          *(float *)((longlong)*pauVar18 + 0xc) = fVar114 + fVar130;
          *(float *)((longlong)*pauVar18 + 0x10) = fVar115 + fVar8;
          *(float *)((longlong)*pauVar18 + 0x14) = fVar116 + fVar73;
          *(float *)((longlong)*pauVar18 + 0x18) = fVar117 + fVar103;
          *(float *)((longlong)*pauVar18 + 0x1c) = fVar118 + fVar5;
          *pauVar38 = auVar111;
          *pauVar34 = auVar107;
          *pauVar31 = auVar105;
          *pauVar30 = auVar100;
          *pauVar20 = auVar74;
          *pauVar17 = auVar66;
          pauVar18 = pauVar18 + 1;
          pauVar38 = pauVar38 + 1;
          pauVar34 = pauVar34 + 1;
          pauVar31 = pauVar31 + 1;
          pauVar30 = pauVar30 + 1;
          pauVar20 = pauVar20 + 1;
          pauVar17 = pauVar17 + 1;
          pauVar24 = pauVar24 + 1;
          uVar15 = uVar15 - 1;
        } while (uVar15 != 0);
      }
      if (local_240 < uVar23) {
        fVar72 = auVar109._0_4_;
        fVar125 = auVar108._0_4_;
        fVar126 = auVar55._0_4_;
        uVar12 = local_240;
        if (3 < uVar23 + uVar25 * -8) {
          uVar23 = ((uVar23 + uVar25 * -8) - 4 >> 2) + 1;
          uVar15 = (ulonglong)uVar23;
          uVar12 = local_240 + uVar23 * 4;
          auVar109 = ZEXT1664(auVar120._0_16_);
          auVar108 = ZEXT1664(auVar123._0_16_);
          auVar55 = ZEXT1664(auVar128._0_16_);
          auVar101 = ZEXT1664(auVar119);
          do {
            fVar130 = *(float *)*pauVar24;
            *(float *)*pauVar18 = fVar130 + *(float *)*pauVar18;
            *(float *)*pauVar38 = fVar130 * fVar72 + *(float *)*pauVar38;
            *(float *)*pauVar34 = fVar130 * fVar125 + *(float *)*pauVar34;
            *(float *)*pauVar31 = fVar130 * fVar126 + *(float *)*pauVar31;
            *(float *)*pauVar30 = fVar130 * fVar125 + *(float *)*pauVar30;
            *(float *)*pauVar20 = fVar130 * fVar72 + *(float *)*pauVar20;
            *(float *)*pauVar17 = fVar130 + *(float *)*pauVar17;
            fVar130 = *(float *)((longlong)*pauVar24 + 4);
            *(float *)((longlong)*pauVar18 + 4) = fVar130 + *(float *)((longlong)*pauVar18 + 4);
            *(float *)((longlong)*pauVar38 + 4) =
                 fVar130 * fVar72 + *(float *)((longlong)*pauVar38 + 4);
            *(float *)((longlong)*pauVar34 + 4) =
                 fVar130 * fVar125 + *(float *)((longlong)*pauVar34 + 4);
            *(float *)((longlong)*pauVar31 + 4) =
                 fVar130 * fVar126 + *(float *)((longlong)*pauVar31 + 4);
            *(float *)((longlong)*pauVar30 + 4) =
                 fVar130 * fVar125 + *(float *)((longlong)*pauVar30 + 4);
            *(float *)((longlong)*pauVar20 + 4) =
                 fVar130 * fVar72 + *(float *)((longlong)*pauVar20 + 4);
            *(float *)((longlong)*pauVar17 + 4) = fVar130 + *(float *)((longlong)*pauVar17 + 4);
            fVar130 = *(float *)((longlong)*pauVar24 + 8);
            *(float *)((longlong)*pauVar18 + 8) = fVar130 + *(float *)((longlong)*pauVar18 + 8);
            *(float *)((longlong)*pauVar38 + 8) =
                 fVar130 * fVar72 + *(float *)((longlong)*pauVar38 + 8);
            *(float *)((longlong)*pauVar34 + 8) =
                 fVar130 * fVar125 + *(float *)((longlong)*pauVar34 + 8);
            *(float *)((longlong)*pauVar31 + 8) =
                 fVar130 * fVar126 + *(float *)((longlong)*pauVar31 + 8);
            *(float *)((longlong)*pauVar30 + 8) =
                 fVar130 * fVar125 + *(float *)((longlong)*pauVar30 + 8);
            *(float *)((longlong)*pauVar20 + 8) =
                 fVar130 * fVar72 + *(float *)((longlong)*pauVar20 + 8);
            *(float *)((longlong)*pauVar17 + 8) = fVar130 + *(float *)((longlong)*pauVar17 + 8);
            fVar130 = *(float *)((longlong)*pauVar24 + 0xc);
            *(float *)((longlong)*pauVar18 + 0xc) = fVar130 + *(float *)((longlong)*pauVar18 + 0xc);
            *(float *)((longlong)*pauVar38 + 0xc) =
                 fVar130 * fVar72 + *(float *)((longlong)*pauVar38 + 0xc);
            *(float *)((longlong)*pauVar34 + 0xc) =
                 fVar130 * fVar125 + *(float *)((longlong)*pauVar34 + 0xc);
            *(float *)((longlong)*pauVar31 + 0xc) =
                 fVar130 * fVar126 + *(float *)((longlong)*pauVar31 + 0xc);
            *(float *)((longlong)*pauVar30 + 0xc) =
                 fVar130 * fVar125 + *(float *)((longlong)*pauVar30 + 0xc);
            *(float *)((longlong)*pauVar20 + 0xc) =
                 fVar130 * fVar72 + *(float *)((longlong)*pauVar20 + 0xc);
            *(float *)((longlong)*pauVar17 + 0xc) = fVar130 + *(float *)((longlong)*pauVar17 + 0xc);
            pauVar24 = (undefined1 (*) [32])((longlong)*pauVar24 + 0x10);
            pauVar17 = (undefined1 (*) [32])((longlong)*pauVar17 + 0x10);
            pauVar20 = (undefined1 (*) [32])((longlong)*pauVar20 + 0x10);
            pauVar30 = (undefined1 (*) [32])((longlong)*pauVar30 + 0x10);
            pauVar31 = (undefined1 (*) [32])((longlong)*pauVar31 + 0x10);
            pauVar34 = (undefined1 (*) [32])((longlong)*pauVar34 + 0x10);
            pauVar38 = (undefined1 (*) [32])((longlong)*pauVar38 + 0x10);
            pauVar18 = (undefined1 (*) [32])((longlong)*pauVar18 + 0x10);
            uVar15 = uVar15 - 1;
          } while (uVar15 != 0);
          uVar23 = local_234;
          if (local_234 <= uVar12) goto LAB_1403881b2;
        }
        lVar13 = (longlong)pauVar24 - (longlong)pauVar17;
        lVar26 = (longlong)pauVar18 - (longlong)pauVar17;
        lVar39 = (longlong)pauVar38 - (longlong)pauVar17;
        lVar35 = (longlong)pauVar34 - (longlong)pauVar17;
        lVar32 = (longlong)pauVar31 - (longlong)pauVar17;
        lVar29 = (longlong)pauVar30 - (longlong)pauVar17;
        lVar14 = (longlong)pauVar20 - (longlong)pauVar17;
        uVar15 = (ulonglong)(uVar23 - uVar12);
        auVar109 = ZEXT1664(auVar120._0_16_);
        auVar108 = ZEXT1664(auVar123._0_16_);
        auVar55 = ZEXT1664(auVar128._0_16_);
        auVar101 = ZEXT1664(auVar119);
        do {
          fVar130 = *(float *)(lVar13 + (longlong)pauVar17);
          *(float *)(lVar26 + (longlong)pauVar17) =
               fVar130 + *(float *)(lVar26 + (longlong)pauVar17);
          *(float *)(lVar39 + (longlong)pauVar17) =
               fVar130 * fVar72 + *(float *)(lVar39 + (longlong)pauVar17);
          *(float *)(lVar35 + (longlong)pauVar17) =
               fVar130 * fVar125 + *(float *)(lVar35 + (longlong)pauVar17);
          *(float *)(lVar32 + (longlong)pauVar17) =
               fVar130 * fVar126 + *(float *)(lVar32 + (longlong)pauVar17);
          *(float *)(lVar29 + (longlong)pauVar17) =
               fVar130 * fVar125 + *(float *)(lVar29 + (longlong)pauVar17);
          *(float *)(lVar14 + (longlong)pauVar17) =
               fVar130 * fVar72 + *(float *)(lVar14 + (longlong)pauVar17);
          *(float *)*pauVar17 = fVar130 + *(float *)*pauVar17;
          pauVar17 = (undefined1 (*) [32])((longlong)*pauVar17 + 4);
          uVar15 = uVar15 - 1;
        } while (uVar15 != 0);
      }
LAB_1403881b2:
      uVar12 = uVar41 - 1;
      puVar40 = local_220;
      uVar41 = uVar41 + 1;
    } while (uVar12 < local_10c);
  }
  fVar126 = DAT_1404deadc;
  fVar125 = DAT_140466950;
  fVar72 = DAT_1404667a0;
  auVar120._0_16_ = vshufps_avx(ZEXT416((uint)local_108),ZEXT416((uint)local_108),0);
  auVar43._16_16_ = auVar120._0_16_;
  auVar43._0_16_ = auVar120._0_16_;
  local_160 = local_230;
  fStack_15c = local_230;
  fStack_158 = local_230;
  fStack_154 = local_230;
  fStack_150 = local_230;
  fStack_14c = local_230;
  fStack_148 = local_230;
  fStack_144 = local_230;
  local_1e0._4_4_ = local_238;
  local_1e0._0_4_ = local_238;
  local_1e0._8_4_ = local_238;
  local_1e0._12_4_ = local_238;
  local_1e0._16_4_ = local_238;
  local_1e0._20_4_ = local_238;
  local_1e0._24_4_ = local_238;
  local_1e0._28_4_ = local_238;
  auVar123._0_16_ = vshufps_avx(auVar101._0_16_,auVar101._0_16_,0);
  local_180._16_16_ = auVar123._0_16_;
  local_180._0_16_ = auVar123._0_16_;
  auVar123._0_16_ = vshufps_avx(ZEXT416((uint)fVar135),ZEXT416((uint)fVar135),0);
  auVar44._16_16_ = auVar123._0_16_;
  auVar44._0_16_ = auVar123._0_16_;
  auVar128._0_16_ = vshufps_avx(ZEXT416((uint)fVar134),ZEXT416((uint)fVar134),0);
  local_1c0._16_16_ = auVar128._0_16_;
  local_1c0._0_16_ = auVar128._0_16_;
  auVar75._4_4_ = DAT_140466950;
  auVar75._0_4_ = DAT_140466950;
  auVar75._8_4_ = DAT_140466950;
  auVar75._12_4_ = DAT_140466950;
  auVar75._16_4_ = DAT_140466950;
  auVar75._20_4_ = DAT_140466950;
  auVar75._24_4_ = DAT_140466950;
  auVar75._28_4_ = DAT_140466950;
  local_108 = 0.125;
  if (local_114 < local_10c) {
    uVar25 = local_23c * 8;
    auVar109 = ZEXT1264(ZEXT812(0));
    uVar41 = local_114 + 3;
    do {
      auVar127 = auVar101._0_16_;
      auVar122 = auVar109._0_16_;
      auVar119 = auVar75._0_16_;
      pauVar19 = (undefined1 (*) [16])
                 ((ulonglong)((uVar41 - 3) * *(int *)(local_208 + 0x14)) + (ulonglong)local_118 * 2
                 + *(longlong *)(local_208 + 0x20));
      lVar14 = *(longlong *)((longlong)*pauVar28 + 0x10);
      pauVar17 = pauVar22;
      if ((lVar14 != 0) && (uVar41 < *(uint *)((longlong)*pauVar28 + 4))) {
        if (*(uint *)*pauVar28 < 4) {
          pauVar17 = (undefined1 (*) [32])0x0;
        }
        else {
          pauVar17 = (undefined1 (*) [32])
                     (lVar14 + 0xc + (ulonglong)(uVar41 * *(int *)((longlong)*pauVar28 + 8)));
        }
      }
      pauVar20 = pauVar22;
      if ((*(longlong *)(puVar40 + 4) != 0) && (uVar41 < puVar40[1])) {
        if (*puVar40 < 4) {
          pauVar20 = (undefined1 (*) [32])0x0;
        }
        else {
          pauVar20 = (undefined1 (*) [32])
                     (*(longlong *)(puVar40 + 4) + 0xc + (ulonglong)(uVar41 * puVar40[2]));
        }
      }
      uVar12 = 0;
      local_1a0._0_4_ = auVar120._0_4_;
      local_1a0._8_4_ = auVar120._8_4_;
      fStack_194 = auVar120._12_4_;
      fStack_18c = auVar120._4_4_;
      local_140._0_4_ = auVar123._0_4_;
      local_140._8_4_ = auVar123._8_4_;
      fStack_134 = auVar123._12_4_;
      fStack_12c = auVar123._4_4_;
      fVar130 = auVar128._0_4_;
      fVar8 = auVar128._4_4_;
      fVar73 = auVar128._8_4_;
      fVar103 = auVar128._12_4_;
      pauVar18 = pauVar17;
      pauVar30 = pauVar20;
      if (3 < local_23c) {
        auVar131 = vshufps_avx(ZEXT416((uint)(DAT_1404debbc - fVar134)),
                               ZEXT416((uint)(DAT_1404debbc - fVar134)),0);
        uVar12 = (local_23c - 4 >> 2) + 1;
        uVar15 = (ulonglong)uVar12;
        uVar12 = uVar12 * 4;
        while( true ) {
          auVar119 = vpunpckhwd_avx(*pauVar19,ZEXT816(0));
          auVar6 = vpunpcklwd_avx(*pauVar19,ZEXT816(0));
          auVar67._16_16_ = auVar119;
          auVar67._0_16_ = auVar6;
          auVar58 = vcvtdq2ps_avx(auVar67);
          fVar5 = auVar131._0_4_;
          fVar112 = auVar131._4_4_;
          fVar113 = auVar131._8_4_;
          fVar114 = auVar131._12_4_;
          auVar76._0_4_ =
               auVar75._0_4_ * *(float *)*pauVar18 * fVar130 +
               DAT_1404deadc * *(float *)*pauVar30 * fVar5;
          auVar76._4_4_ =
               auVar75._4_4_ * *(float *)((longlong)*pauVar18 + 4) * fVar8 +
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 4) * fVar112;
          auVar76._8_4_ =
               auVar75._8_4_ * *(float *)((longlong)*pauVar18 + 8) * fVar73 +
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 8) * fVar113;
          auVar76._12_4_ =
               auVar75._12_4_ * *(float *)((longlong)*pauVar18 + 0xc) * fVar103 +
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 0xc) * fVar114;
          auVar76._16_4_ =
               auVar75._16_4_ * *(float *)((longlong)*pauVar18 + 0x10) * fVar130 +
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 0x10) * fVar5;
          auVar76._20_4_ =
               auVar75._20_4_ * *(float *)((longlong)*pauVar18 + 0x14) * fVar8 +
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 0x14) * fVar112;
          auVar76._24_4_ =
               auVar75._24_4_ * *(float *)((longlong)*pauVar18 + 0x18) * fVar73 +
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 0x18) * fVar113;
          auVar76._28_4_ =
               auVar75._28_4_ * *(float *)((longlong)*pauVar18 + 0x1c) * fVar103 +
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 0x1c) * fVar114;
          auVar59 = vsubps_avx(auVar58,auVar76);
          auVar57 = vcmpps_avx(auVar59,local_1e0,0x1e);
          auVar77._0_4_ = auVar59._0_4_ * (float)local_1a0._0_4_;
          auVar77._4_4_ = auVar59._4_4_ * fStack_18c;
          auVar77._8_4_ = auVar59._8_4_ * (float)local_1a0._8_4_;
          auVar77._12_4_ = auVar59._12_4_ * fStack_194;
          auVar77._16_4_ = auVar59._16_4_ * (float)local_1a0._0_4_;
          auVar77._20_4_ = auVar59._20_4_ * fStack_18c;
          auVar77._24_4_ = auVar59._24_4_ * (float)local_1a0._8_4_;
          auVar77._28_4_ = auVar59._28_4_ * fStack_194;
          auVar61 = vcmpps_avx(auVar59,local_180,0x11);
          auVar45._0_4_ = auVar59._0_4_ * local_230;
          auVar45._4_4_ = auVar59._4_4_ * local_230;
          auVar45._8_4_ = auVar59._8_4_ * local_230;
          auVar45._12_4_ = auVar59._12_4_ * local_230;
          auVar45._16_4_ = auVar59._16_4_ * local_230;
          auVar45._20_4_ = auVar59._20_4_ * local_230;
          auVar45._24_4_ = auVar59._24_4_ * local_230;
          auVar45._28_4_ = auVar59._28_4_ * local_230;
          auVar61 = vblendvps_avx(ZEXT832(0) << 0x20,auVar45,auVar61);
          auVar61 = vblendvps_avx(auVar61,auVar77,auVar57);
          auVar46._0_4_ = auVar61._0_4_ * (float)local_140._0_4_ + auVar58._0_4_;
          auVar46._4_4_ = auVar61._4_4_ * fStack_12c + auVar58._4_4_;
          auVar46._8_4_ = auVar61._8_4_ * (float)local_140._8_4_ + auVar58._8_4_;
          auVar46._12_4_ = auVar61._12_4_ * fStack_134 + auVar58._12_4_;
          auVar46._16_4_ = auVar61._16_4_ * (float)local_140._0_4_ + auVar58._16_4_;
          auVar46._20_4_ = auVar61._20_4_ * fStack_12c + auVar58._20_4_;
          auVar46._24_4_ = auVar61._24_4_ * (float)local_140._8_4_ + auVar58._24_4_;
          auVar46._28_4_ = auVar61._28_4_ * fStack_134 + auVar58._28_4_;
          auVar61 = vroundps_avx(auVar46,1);
          auVar61 = vminps_avx(auVar61,_DAT_140468ca0);
          auVar61 = vmaxps_avx(auVar61,ZEXT432(0) << 0x20);
          auVar61 = vcvtps2dq_avx(auVar61);
          auVar119 = vpackusdw_avx(auVar61._0_16_,auVar61._16_16_);
          *pauVar19 = auVar119;
          auVar119 = vpunpckhwd_avx(pauVar19[1],ZEXT816(0));
          auVar6 = vpunpcklwd_avx(pauVar19[1],ZEXT816(0));
          auVar68._16_16_ = auVar119;
          auVar68._0_16_ = auVar6;
          auVar58 = vcvtdq2ps_avx(auVar68);
          pfVar27 = (float *)((longlong)pauVar17 + (0x20 - (longlong)pauVar20) + (longlong)pauVar30)
          ;
          auVar78._0_4_ =
               DAT_140466950 * *pfVar27 * fVar130 + DAT_1404deadc * *(float *)pauVar30[1] * fVar5;
          auVar78._4_4_ =
               DAT_140466950 * pfVar27[1] * fVar8 +
               DAT_1404deadc * *(float *)(pauVar30[1] + 4) * fVar112;
          auVar78._8_4_ =
               DAT_140466950 * pfVar27[2] * fVar73 +
               DAT_1404deadc * *(float *)(pauVar30[1] + 8) * fVar113;
          auVar78._12_4_ =
               DAT_140466950 * pfVar27[3] * fVar103 +
               DAT_1404deadc * *(float *)(pauVar30[1] + 0xc) * fVar114;
          auVar78._16_4_ =
               DAT_140466950 * pfVar27[4] * fVar130 +
               DAT_1404deadc * *(float *)(pauVar30[1] + 0x10) * fVar5;
          auVar78._20_4_ =
               DAT_140466950 * pfVar27[5] * fVar8 +
               DAT_1404deadc * *(float *)(pauVar30[1] + 0x14) * fVar112;
          auVar78._24_4_ =
               DAT_140466950 * pfVar27[6] * fVar73 +
               DAT_1404deadc * *(float *)(pauVar30[1] + 0x18) * fVar113;
          auVar78._28_4_ =
               DAT_140466950 * pfVar27[7] * fVar103 +
               DAT_1404deadc * *(float *)(pauVar30[1] + 0x1c) * fVar114;
          auVar59 = vsubps_avx(auVar58,auVar78);
          auVar57 = vcmpps_avx(auVar59,local_1e0,0x1e);
          auVar79._0_4_ = auVar59._0_4_ * (float)local_1a0._0_4_;
          auVar79._4_4_ = auVar59._4_4_ * fStack_18c;
          auVar79._8_4_ = auVar59._8_4_ * (float)local_1a0._8_4_;
          auVar79._12_4_ = auVar59._12_4_ * fStack_194;
          auVar79._16_4_ = auVar59._16_4_ * (float)local_1a0._0_4_;
          auVar79._20_4_ = auVar59._20_4_ * fStack_18c;
          auVar79._24_4_ = auVar59._24_4_ * (float)local_1a0._8_4_;
          auVar79._28_4_ = auVar59._28_4_ * fStack_194;
          auVar61 = vcmpps_avx(auVar59,local_180,0x11);
          auVar47._0_4_ = auVar59._0_4_ * local_230;
          auVar47._4_4_ = auVar59._4_4_ * local_230;
          auVar47._8_4_ = auVar59._8_4_ * local_230;
          auVar47._12_4_ = auVar59._12_4_ * local_230;
          auVar47._16_4_ = auVar59._16_4_ * local_230;
          auVar47._20_4_ = auVar59._20_4_ * local_230;
          auVar47._24_4_ = auVar59._24_4_ * local_230;
          auVar47._28_4_ = auVar59._28_4_ * local_230;
          auVar61 = vblendvps_avx(ZEXT832(0) << 0x20,auVar47,auVar61);
          auVar61 = vblendvps_avx(auVar61,auVar79,auVar57);
          auVar48._0_4_ = auVar61._0_4_ * (float)local_140._0_4_ + auVar58._0_4_;
          auVar48._4_4_ = auVar61._4_4_ * fStack_12c + auVar58._4_4_;
          auVar48._8_4_ = auVar61._8_4_ * (float)local_140._8_4_ + auVar58._8_4_;
          auVar48._12_4_ = auVar61._12_4_ * fStack_134 + auVar58._12_4_;
          auVar48._16_4_ = auVar61._16_4_ * (float)local_140._0_4_ + auVar58._16_4_;
          auVar48._20_4_ = auVar61._20_4_ * fStack_12c + auVar58._20_4_;
          auVar48._24_4_ = auVar61._24_4_ * (float)local_140._8_4_ + auVar58._24_4_;
          auVar48._28_4_ = auVar61._28_4_ * fStack_134 + auVar58._28_4_;
          auVar61 = vroundps_avx(auVar48,1);
          auVar61 = vminps_avx(auVar61,_DAT_140468ca0);
          auVar61 = vmaxps_avx(auVar61,ZEXT432(0) << 0x20);
          auVar61 = vcvtps2dq_avx(auVar61);
          auVar119 = vpackusdw_avx(auVar61._0_16_,auVar61._16_16_);
          pauVar19[1] = auVar119;
          auVar119 = vpunpckhwd_avx(pauVar19[2],ZEXT816(0));
          auVar6 = vpunpcklwd_avx(pauVar19[2],ZEXT816(0));
          auVar69._16_16_ = auVar119;
          auVar69._0_16_ = auVar6;
          auVar58 = vcvtdq2ps_avx(auVar69);
          pfVar27 = (float *)((longlong)pauVar17 + (0x40 - (longlong)pauVar20) + (longlong)pauVar30)
          ;
          auVar80._0_4_ =
               DAT_140466950 * *pfVar27 * fVar130 + DAT_1404deadc * *(float *)pauVar30[2] * fVar5;
          auVar80._4_4_ =
               DAT_140466950 * pfVar27[1] * fVar8 +
               DAT_1404deadc * *(float *)(pauVar30[2] + 4) * fVar112;
          auVar80._8_4_ =
               DAT_140466950 * pfVar27[2] * fVar73 +
               DAT_1404deadc * *(float *)(pauVar30[2] + 8) * fVar113;
          auVar80._12_4_ =
               DAT_140466950 * pfVar27[3] * fVar103 +
               DAT_1404deadc * *(float *)(pauVar30[2] + 0xc) * fVar114;
          auVar80._16_4_ =
               DAT_140466950 * pfVar27[4] * fVar130 +
               DAT_1404deadc * *(float *)(pauVar30[2] + 0x10) * fVar5;
          auVar80._20_4_ =
               DAT_140466950 * pfVar27[5] * fVar8 +
               DAT_1404deadc * *(float *)(pauVar30[2] + 0x14) * fVar112;
          auVar80._24_4_ =
               DAT_140466950 * pfVar27[6] * fVar73 +
               DAT_1404deadc * *(float *)(pauVar30[2] + 0x18) * fVar113;
          auVar80._28_4_ =
               DAT_140466950 * pfVar27[7] * fVar103 +
               DAT_1404deadc * *(float *)(pauVar30[2] + 0x1c) * fVar114;
          auVar59 = vsubps_avx(auVar58,auVar80);
          auVar57 = vcmpps_avx(auVar59,local_1e0,0x1e);
          auVar81._0_4_ = auVar59._0_4_ * (float)local_1a0._0_4_;
          auVar81._4_4_ = auVar59._4_4_ * fStack_18c;
          auVar81._8_4_ = auVar59._8_4_ * (float)local_1a0._8_4_;
          auVar81._12_4_ = auVar59._12_4_ * fStack_194;
          auVar81._16_4_ = auVar59._16_4_ * (float)local_1a0._0_4_;
          auVar81._20_4_ = auVar59._20_4_ * fStack_18c;
          auVar81._24_4_ = auVar59._24_4_ * (float)local_1a0._8_4_;
          auVar81._28_4_ = auVar59._28_4_ * fStack_194;
          auVar61 = vcmpps_avx(auVar59,local_180,0x11);
          auVar49._0_4_ = auVar59._0_4_ * local_230;
          auVar49._4_4_ = auVar59._4_4_ * local_230;
          auVar49._8_4_ = auVar59._8_4_ * local_230;
          auVar49._12_4_ = auVar59._12_4_ * local_230;
          auVar49._16_4_ = auVar59._16_4_ * local_230;
          auVar49._20_4_ = auVar59._20_4_ * local_230;
          auVar49._24_4_ = auVar59._24_4_ * local_230;
          auVar49._28_4_ = auVar59._28_4_ * local_230;
          auVar61 = vblendvps_avx(ZEXT832(0) << 0x20,auVar49,auVar61);
          auVar61 = vblendvps_avx(auVar61,auVar81,auVar57);
          auVar50._0_4_ = auVar61._0_4_ * (float)local_140._0_4_ + auVar58._0_4_;
          auVar50._4_4_ = auVar61._4_4_ * fStack_12c + auVar58._4_4_;
          auVar50._8_4_ = auVar61._8_4_ * (float)local_140._8_4_ + auVar58._8_4_;
          auVar50._12_4_ = auVar61._12_4_ * fStack_134 + auVar58._12_4_;
          auVar50._16_4_ = auVar61._16_4_ * (float)local_140._0_4_ + auVar58._16_4_;
          auVar50._20_4_ = auVar61._20_4_ * fStack_12c + auVar58._20_4_;
          auVar50._24_4_ = auVar61._24_4_ * (float)local_140._8_4_ + auVar58._24_4_;
          auVar50._28_4_ = auVar61._28_4_ * fStack_134 + auVar58._28_4_;
          auVar61 = vroundps_avx(auVar50,1);
          auVar61 = vminps_avx(auVar61,_DAT_140468ca0);
          auVar61 = vmaxps_avx(auVar61,ZEXT432(0) << 0x20);
          auVar61 = vcvtps2dq_avx(auVar61);
          auVar119 = vpackusdw_avx(auVar61._0_16_,auVar61._16_16_);
          pauVar19[2] = auVar119;
          auVar119 = vpunpckhwd_avx(pauVar19[3],ZEXT816(0));
          auVar6 = vpunpcklwd_avx(pauVar19[3],ZEXT816(0));
          auVar70._16_16_ = auVar119;
          auVar70._0_16_ = auVar6;
          auVar58 = vcvtdq2ps_avx(auVar70);
          pfVar27 = (float *)((longlong)pauVar17 + (0x60 - (longlong)pauVar20) + (longlong)pauVar30)
          ;
          auVar82._0_4_ =
               DAT_140466950 * *pfVar27 * fVar130 + DAT_1404deadc * *(float *)pauVar30[3] * fVar5;
          auVar82._4_4_ =
               DAT_140466950 * pfVar27[1] * fVar8 +
               DAT_1404deadc * *(float *)(pauVar30[3] + 4) * fVar112;
          auVar82._8_4_ =
               DAT_140466950 * pfVar27[2] * fVar73 +
               DAT_1404deadc * *(float *)(pauVar30[3] + 8) * fVar113;
          auVar82._12_4_ =
               DAT_140466950 * pfVar27[3] * fVar103 +
               DAT_1404deadc * *(float *)(pauVar30[3] + 0xc) * fVar114;
          auVar82._16_4_ =
               DAT_140466950 * pfVar27[4] * fVar130 +
               DAT_1404deadc * *(float *)(pauVar30[3] + 0x10) * fVar5;
          auVar82._20_4_ =
               DAT_140466950 * pfVar27[5] * fVar8 +
               DAT_1404deadc * *(float *)(pauVar30[3] + 0x14) * fVar112;
          auVar82._24_4_ =
               DAT_140466950 * pfVar27[6] * fVar73 +
               DAT_1404deadc * *(float *)(pauVar30[3] + 0x18) * fVar113;
          auVar82._28_4_ =
               DAT_140466950 * pfVar27[7] * fVar103 +
               DAT_1404deadc * *(float *)(pauVar30[3] + 0x1c) * fVar114;
          auVar59 = vsubps_avx(auVar58,auVar82);
          auVar57 = vcmpps_avx(auVar59,local_1e0,0x1e);
          auVar83._0_4_ = auVar59._0_4_ * (float)local_1a0._0_4_;
          auVar83._4_4_ = auVar59._4_4_ * fStack_18c;
          auVar83._8_4_ = auVar59._8_4_ * (float)local_1a0._8_4_;
          auVar83._12_4_ = auVar59._12_4_ * fStack_194;
          auVar83._16_4_ = auVar59._16_4_ * (float)local_1a0._0_4_;
          auVar83._20_4_ = auVar59._20_4_ * fStack_18c;
          auVar83._24_4_ = auVar59._24_4_ * (float)local_1a0._8_4_;
          auVar83._28_4_ = auVar59._28_4_ * fStack_194;
          auVar61 = vcmpps_avx(auVar59,local_180,0x11);
          auVar51._0_4_ = auVar59._0_4_ * local_230;
          auVar51._4_4_ = auVar59._4_4_ * local_230;
          auVar51._8_4_ = auVar59._8_4_ * local_230;
          auVar51._12_4_ = auVar59._12_4_ * local_230;
          auVar51._16_4_ = auVar59._16_4_ * local_230;
          auVar51._20_4_ = auVar59._20_4_ * local_230;
          auVar51._24_4_ = auVar59._24_4_ * local_230;
          auVar51._28_4_ = auVar59._28_4_ * local_230;
          auVar61 = vblendvps_avx(ZEXT832(0) << 0x20,auVar51,auVar61);
          auVar61 = vblendvps_avx(auVar61,auVar83,auVar57);
          auVar52._0_4_ = auVar61._0_4_ * (float)local_140._0_4_ + auVar58._0_4_;
          auVar52._4_4_ = auVar61._4_4_ * fStack_12c + auVar58._4_4_;
          auVar52._8_4_ = auVar61._8_4_ * (float)local_140._8_4_ + auVar58._8_4_;
          auVar52._12_4_ = auVar61._12_4_ * fStack_134 + auVar58._12_4_;
          auVar52._16_4_ = auVar61._16_4_ * (float)local_140._0_4_ + auVar58._16_4_;
          auVar52._20_4_ = auVar61._20_4_ * fStack_12c + auVar58._20_4_;
          auVar52._24_4_ = auVar61._24_4_ * (float)local_140._8_4_ + auVar58._24_4_;
          auVar52._28_4_ = auVar61._28_4_ * fStack_134 + auVar58._28_4_;
          auVar61 = vroundps_avx(auVar52,1);
          auVar61 = vminps_avx(auVar61,_DAT_140468ca0);
          auVar119 = auVar61._0_16_;
          auVar61 = vmaxps_avx(auVar61,ZEXT432(0) << 0x20);
          auVar61 = vcvtps2dq_avx(auVar61);
          auVar6 = vpackusdw_avx(auVar61._0_16_,auVar61._16_16_);
          pauVar19[3] = auVar6;
          pauVar30 = pauVar30 + 4;
          pauVar18 = pauVar18 + 4;
          pauVar19 = pauVar19 + 4;
          uVar15 = uVar15 - 1;
          if (uVar15 == 0) break;
          auVar75._4_4_ = DAT_140466950;
          auVar75._0_4_ = DAT_140466950;
          auVar75._8_4_ = DAT_140466950;
          auVar75._12_4_ = DAT_140466950;
          auVar75._16_4_ = DAT_140466950;
          auVar75._20_4_ = DAT_140466950;
          auVar75._24_4_ = DAT_140466950;
          auVar75._28_4_ = DAT_140466950;
        }
      }
      if (uVar12 < local_23c) {
        auVar131 = vshufps_avx(ZEXT416((uint)(DAT_1404debbc - fVar134)),
                               ZEXT416((uint)(DAT_1404debbc - fVar134)),0);
        uVar15 = (ulonglong)(local_23c - uVar12);
        do {
          auVar119 = vpunpckhwd_avx(*pauVar19,ZEXT816(0));
          auVar6 = vpunpcklwd_avx(*pauVar19,ZEXT816(0));
          auVar71._16_16_ = auVar119;
          auVar71._0_16_ = auVar6;
          auVar58 = vcvtdq2ps_avx(auVar71);
          auVar84._0_4_ =
               DAT_1404deadc * *(float *)*pauVar30 * auVar131._0_4_ +
               DAT_140466950 * *(float *)*pauVar18 * fVar130;
          auVar84._4_4_ =
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 4) * auVar131._4_4_ +
               DAT_140466950 * *(float *)((longlong)*pauVar18 + 4) * fVar8;
          auVar84._8_4_ =
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 8) * auVar131._8_4_ +
               DAT_140466950 * *(float *)((longlong)*pauVar18 + 8) * fVar73;
          auVar84._12_4_ =
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 0xc) * auVar131._12_4_ +
               DAT_140466950 * *(float *)((longlong)*pauVar18 + 0xc) * fVar103;
          auVar84._16_4_ =
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 0x10) * auVar131._0_4_ +
               DAT_140466950 * *(float *)((longlong)*pauVar18 + 0x10) * fVar130;
          auVar84._20_4_ =
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 0x14) * auVar131._4_4_ +
               DAT_140466950 * *(float *)((longlong)*pauVar18 + 0x14) * fVar8;
          auVar84._24_4_ =
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 0x18) * auVar131._8_4_ +
               DAT_140466950 * *(float *)((longlong)*pauVar18 + 0x18) * fVar73;
          auVar84._28_4_ =
               DAT_1404deadc * *(float *)((longlong)*pauVar30 + 0x1c) * auVar131._12_4_ +
               DAT_140466950 * *(float *)((longlong)*pauVar18 + 0x1c) * fVar103;
          auVar59 = vsubps_avx(auVar58,auVar84);
          auVar57 = vcmpps_avx(auVar59,local_1e0,0x1e);
          auVar85._0_4_ = auVar59._0_4_ * (float)local_1a0._0_4_;
          auVar85._4_4_ = auVar59._4_4_ * fStack_18c;
          auVar85._8_4_ = auVar59._8_4_ * (float)local_1a0._8_4_;
          auVar85._12_4_ = auVar59._12_4_ * fStack_194;
          auVar85._16_4_ = auVar59._16_4_ * (float)local_1a0._0_4_;
          auVar85._20_4_ = auVar59._20_4_ * fStack_18c;
          auVar85._24_4_ = auVar59._24_4_ * (float)local_1a0._8_4_;
          auVar85._28_4_ = auVar59._28_4_ * fStack_194;
          auVar61 = vcmpps_avx(auVar59,local_180,0x11);
          auVar53._0_4_ = auVar59._0_4_ * local_230;
          auVar53._4_4_ = auVar59._4_4_ * local_230;
          auVar53._8_4_ = auVar59._8_4_ * local_230;
          auVar53._12_4_ = auVar59._12_4_ * local_230;
          auVar53._16_4_ = auVar59._16_4_ * local_230;
          auVar53._20_4_ = auVar59._20_4_ * local_230;
          auVar53._24_4_ = auVar59._24_4_ * local_230;
          auVar53._28_4_ = auVar59._28_4_ * local_230;
          auVar61 = vblendvps_avx(ZEXT832(0) << 0x20,auVar53,auVar61);
          auVar61 = vblendvps_avx(auVar61,auVar85,auVar57);
          auVar54._0_4_ = auVar61._0_4_ * (float)local_140._0_4_ + auVar58._0_4_;
          auVar54._4_4_ = auVar61._4_4_ * fStack_12c + auVar58._4_4_;
          auVar54._8_4_ = auVar61._8_4_ * (float)local_140._8_4_ + auVar58._8_4_;
          auVar54._12_4_ = auVar61._12_4_ * fStack_134 + auVar58._12_4_;
          auVar54._16_4_ = auVar61._16_4_ * (float)local_140._0_4_ + auVar58._16_4_;
          auVar54._20_4_ = auVar61._20_4_ * fStack_12c + auVar58._20_4_;
          auVar54._24_4_ = auVar61._24_4_ * (float)local_140._8_4_ + auVar58._24_4_;
          auVar54._28_4_ = auVar61._28_4_ * fStack_134 + auVar58._28_4_;
          auVar61 = vroundps_avx(auVar54,1);
          auVar61 = vminps_avx(auVar61,_DAT_140468ca0);
          auVar119 = auVar61._0_16_;
          auVar61 = vmaxps_avx(auVar61,ZEXT432(0) << 0x20);
          auVar61 = vcvtps2dq_avx(auVar61);
          auVar6 = vpackusdw_avx(auVar61._0_16_,auVar61._16_16_);
          *pauVar19 = auVar6;
          pauVar30 = pauVar30 + 1;
          pauVar18 = pauVar18 + 1;
          pauVar19 = pauVar19 + 1;
          uVar15 = uVar15 - 1;
        } while (uVar15 != 0);
      }
      if (uVar25 < uVar23) {
        uVar21 = 0;
        fVar130 = auVar101._0_4_;
        uVar12 = uVar25;
        if (3 < uVar23 + local_23c * -8) {
          fVar8 = DAT_1404debbc - fVar134;
          uVar12 = ((uVar23 + local_23c * -8) - 4 >> 2) + 1;
          uVar15 = (ulonglong)uVar12;
          uVar12 = uVar25 + uVar12 * 4;
          auVar94 = ZEXT1664(auVar119);
          auVar109 = ZEXT1664(auVar122);
          auVar101 = ZEXT1664(auVar127);
          do {
            fVar103 = (float)*(ushort *)*pauVar19;
            auVar86._16_48_ = auVar94._16_48_;
            auVar86._0_16_ = ZEXT416((uint)fVar103);
            auVar87._4_60_ = auVar86._4_60_;
            fVar73 = fVar103 - (*(float *)*pauVar30 * fVar126 * fVar8 +
                               *(float *)*pauVar18 * fVar125 * fVar134);
            if (fVar73 <= local_238) {
              if (fVar130 <= fVar73) {
                auVar87._16_48_ = auVar86._16_48_;
                auVar87._0_16_ = auVar122;
              }
              else {
                auVar87._0_4_ = fVar73 * local_230;
              }
            }
            else {
              auVar87._0_4_ = fVar73 * fVar56;
            }
            fVar103 = auVar87._0_4_ * fVar135 + fVar103;
            uVar11 = uVar21;
            if ((0.0 <= fVar103) && (uVar11 = 0x7fff, fVar103 <= fVar72)) {
              uVar11 = (short)(int)fVar103;
            }
            *(undefined2 *)*pauVar19 = uVar11;
            fVar103 = (float)*(ushort *)(*pauVar19 + 2);
            auVar88._16_48_ = auVar87._16_48_;
            auVar88._0_16_ = ZEXT416((uint)fVar103);
            auVar89._4_60_ = auVar88._4_60_;
            fVar73 = fVar103 - (*(float *)((longlong)*pauVar18 + 4) * fVar125 * fVar134 +
                               *(float *)((longlong)*pauVar30 + 4) * fVar126 * fVar8);
            if (fVar73 <= local_238) {
              if (fVar130 <= fVar73) {
                auVar89._16_48_ = auVar88._16_48_;
                auVar89._0_16_ = auVar122;
              }
              else {
                auVar89._0_4_ = fVar73 * local_230;
              }
            }
            else {
              auVar89._0_4_ = fVar73 * fVar56;
            }
            fVar103 = auVar89._0_4_ * fVar135 + fVar103;
            uVar11 = uVar21;
            if ((0.0 <= fVar103) && (uVar11 = 0x7fff, fVar103 <= fVar72)) {
              uVar11 = (short)(int)fVar103;
            }
            *(undefined2 *)(*pauVar19 + 2) = uVar11;
            fVar103 = (float)*(ushort *)(*pauVar19 + 4);
            auVar90._16_48_ = auVar89._16_48_;
            auVar90._0_16_ = ZEXT416((uint)fVar103);
            auVar91._4_60_ = auVar90._4_60_;
            fVar73 = fVar103 - (*(float *)((longlong)*pauVar18 + 8) * fVar125 * fVar134 +
                               *(float *)((longlong)*pauVar30 + 8) * fVar126 * fVar8);
            if (fVar73 <= local_238) {
              if (fVar130 <= fVar73) {
                auVar91._16_48_ = auVar90._16_48_;
                auVar91._0_16_ = auVar122;
              }
              else {
                auVar91._0_4_ = fVar73 * local_230;
              }
            }
            else {
              auVar91._0_4_ = fVar73 * fVar56;
            }
            fVar103 = auVar91._0_4_ * fVar135 + fVar103;
            uVar11 = uVar21;
            if ((0.0 <= fVar103) && (uVar11 = 0x7fff, fVar103 <= fVar72)) {
              uVar11 = (short)(int)fVar103;
            }
            *(undefined2 *)(*pauVar19 + 4) = uVar11;
            fVar103 = (float)*(ushort *)(*pauVar19 + 6);
            auVar92._16_48_ = auVar91._16_48_;
            auVar92._0_16_ = ZEXT416((uint)fVar103);
            auVar93._4_60_ = auVar92._4_60_;
            fVar73 = fVar103 - (*(float *)((longlong)*pauVar18 + 0xc) * fVar125 * fVar134 +
                               *(float *)((longlong)*pauVar30 + 0xc) * fVar126 * fVar8);
            if (fVar73 <= local_238) {
              if (fVar130 <= fVar73) {
                auVar93._16_48_ = auVar92._16_48_;
                auVar93._0_16_ = auVar122;
              }
              else {
                auVar93._0_4_ = fVar73 * local_230;
              }
            }
            else {
              auVar93._0_4_ = fVar73 * fVar56;
            }
            auVar94._4_60_ = auVar93._4_60_;
            auVar94._0_4_ = auVar93._0_4_ * fVar135 + fVar103;
            auVar119 = auVar94._0_16_;
            uVar11 = 0;
            if ((0.0 <= auVar94._0_4_) && (uVar11 = 0x7fff, auVar94._0_4_ <= fVar72)) {
              uVar11 = (short)(int)auVar94._0_4_;
            }
            *(undefined2 *)(*pauVar19 + 6) = uVar11;
            pauVar30 = (undefined1 (*) [32])((longlong)*pauVar30 + 0x10);
            pauVar18 = (undefined1 (*) [32])((longlong)*pauVar18 + 0x10);
            pauVar19 = (undefined1 (*) [16])(*pauVar19 + 8);
            uVar15 = uVar15 - 1;
          } while (uVar15 != 0);
          if (uVar23 <= uVar12) goto LAB_140388a4d;
        }
        fVar8 = DAT_1404debbc - fVar134;
        lVar14 = (longlong)pauVar30 - (longlong)pauVar18;
        uVar15 = (ulonglong)(uVar23 - uVar12);
        auVar97 = ZEXT1664(auVar119);
        auVar109 = ZEXT1664(auVar122);
        auVar101 = ZEXT1664(auVar127);
        do {
          fVar103 = (float)*(ushort *)*pauVar19;
          auVar95._16_48_ = auVar97._16_48_;
          auVar95._0_16_ = ZEXT416((uint)fVar103);
          auVar96._4_60_ = auVar95._4_60_;
          fVar73 = fVar103 - (*(float *)(lVar14 + (longlong)pauVar18) * fVar126 * fVar8 +
                             *(float *)*pauVar18 * fVar125 * fVar134);
          if (fVar73 <= local_238) {
            if (fVar130 <= fVar73) {
              auVar96._16_48_ = auVar95._16_48_;
              auVar96._0_16_ = auVar122;
            }
            else {
              auVar96._0_4_ = fVar73 * local_230;
            }
          }
          else {
            auVar96._0_4_ = fVar73 * fVar56;
          }
          auVar97._4_60_ = auVar96._4_60_;
          auVar97._0_4_ = auVar96._0_4_ * fVar135 + fVar103;
          uVar21 = 0;
          if ((0.0 <= auVar97._0_4_) && (uVar21 = 0x7fff, auVar97._0_4_ <= fVar72)) {
            uVar21 = (short)(int)auVar97._0_4_;
          }
          *(undefined2 *)*pauVar19 = uVar21;
          pauVar18 = (undefined1 (*) [32])((longlong)*pauVar18 + 4);
          pauVar19 = (undefined1 (*) [16])(*pauVar19 + 2);
          uVar15 = uVar15 - 1;
        } while (uVar15 != 0);
      }
LAB_140388a4d:
      uVar12 = uVar41 - 2;
      auVar75._4_4_ = DAT_140466950;
      auVar75._0_4_ = DAT_140466950;
      auVar75._8_4_ = DAT_140466950;
      auVar75._12_4_ = DAT_140466950;
      auVar75._16_4_ = DAT_140466950;
      auVar75._20_4_ = DAT_140466950;
      auVar75._24_4_ = DAT_140466950;
      auVar75._28_4_ = DAT_140466950;
      uVar41 = uVar41 + 1;
    } while (uVar12 < local_10c);
  }
  lVar14 = *(longlong *)((longlong)*pauVar28 + 0x10);
  _local_1a0 = auVar43;
  _local_140 = auVar44;
  if (lVar14 != 0) {
    uVar16 = FUN_1401540a0();
    FUN_140153f60(uVar16,lVar14);
  }
  operator_delete(pauVar28);
  puVar10 = local_228;
  lVar14 = *(longlong *)(local_228 + 4);
  if (lVar14 != 0) {
    uVar16 = FUN_1401540a0();
    FUN_140153f60(uVar16,lVar14);
  }
  operator_delete(puVar10);
  if (puVar40 != (uint *)0x0) {
    lVar14 = *(longlong *)(puVar40 + 4);
    if (lVar14 != 0) {
      uVar16 = FUN_1401540a0();
      FUN_140153f60(uVar16,lVar14);
    }
    operator_delete(puVar40);
  }
  *(uint *)(local_200 + 0x30) = local_118;
  *(int *)(local_200 + 0x38) = local_110;
  *(uint *)(local_200 + 0x34) = local_114;
  *(uint *)(local_200 + 0x3c) = local_10c;
  return 0;
}



// ===== depth0 FUN_1401869f0 @ 0x1401869f0 rva=0x1869f0 size=256 =====

undefined8 FUN_1401869f0(int *param_1,int param_2,int param_3)

{
  longlong lVar1;
  longlong *plVar2;
  int iVar3;
  undefined8 uVar4;
  longlong *_Dst;
  longlong *local_28 [2];
  
  lVar1 = *(longlong *)(param_1 + 4);
  if (lVar1 != 0) {
    EnterCriticalSection((LPCRITICAL_SECTION)&DAT_14055d108);
    for (_Dst = DAT_14055d138; _Dst != DAT_14055d140; _Dst = _Dst + 1) {
      plVar2 = (longlong *)*_Dst;
      local_28[0] = plVar2;
      if ((plVar2 != (longlong *)0x0) && (lVar1 == *plVar2)) {
        memmove(_Dst,_Dst + 1,(longlong)DAT_14055d140 - (longlong)(_Dst + 1));
        DAT_14055d140 = DAT_14055d140 + -1;
        if (DAT_14055d158 == DAT_14055d160) {
          FUN_140045dc0(&DAT_14055d150,DAT_14055d158,local_28);
        }
        else {
          *DAT_14055d158 = (longlong)plVar2;
          DAT_14055d158 = DAT_14055d158 + 1;
        }
        break;
      }
    }
    LeaveCriticalSection((LPCRITICAL_SECTION)&DAT_14055d108);
  }
  *param_1 = param_2;
  param_1[1] = param_3;
  iVar3 = (param_2 * 4 + 0x1fU & 0xffffffe0) + 0x20;
  param_1[2] = iVar3;
  param_1[3] = iVar3 * param_3;
  uVar4 = FUN_140153e00(&PTR_vftable_14055d100);
  *(undefined8 *)(param_1 + 4) = uVar4;
  return 1;
}



// ===== depth0 FUN_140152710 @ 0x140152710 rva=0x152710 size=17 =====

undefined8 FUN_140152710(longlong param_1,int param_2)

{
  if (param_2 < 4) {
    return *(undefined8 *)(param_1 + 8 + (longlong)param_2 * 8);
  }
  return 0;
}



// ===== depth0 FUN_140168b60 @ 0x140168b60 rva=0x168b60 size=2785 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */

undefined8
FUN_140168b60(undefined8 param_1,longlong *param_2,longlong param_3,int param_4,int param_5)

{
  LPCRITICAL_SECTION lpCriticalSection;
  longlong lVar1;
  bool bVar2;
  bool bVar3;
  double dVar4;
  double dVar5;
  char cVar6;
  ushort uVar7;
  short sVar8;
  short sVar9;
  undefined4 uVar10;
  int iVar11;
  undefined4 uVar12;
  undefined4 uVar13;
  double *pdVar14;
  undefined8 uVar15;
  undefined8 *puVar16;
  uint uVar17;
  int iVar18;
  longlong lVar19;
  short sVar20;
  undefined1 auStack_108 [36];
  int local_e4;
  int local_e0;
  longlong local_d8;
  longlong local_d0;
  double local_c8;
  double local_c0;
  undefined8 local_b8 [2];
  undefined8 local_a8 [2];
  undefined8 local_98 [2];
  char *local_88;
  ulonglong local_80;
  
  local_80 = DAT_140559440 ^ (ulonglong)auStack_108;
  if (param_2 == (longlong *)0x0) {
    uVar15 = 4;
  }
  else {
    local_e0 = param_4;
    local_d0 = param_3;
    uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x8000,0);
    *(undefined4 *)(param_3 + 0x1e0) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x8001,0);
    *(undefined4 *)(param_3 + 0x1e4) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x8002,0);
    *(undefined4 *)(param_3 + 0x1e8) = uVar10;
    uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x8013,0);
    *(uint *)(param_3 + 0x1ec) = (uint)uVar7;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8014,0);
    *(undefined4 *)(param_3 + 0x1f0) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8015,0);
    *(undefined4 *)(param_3 + 500) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8018,0);
    *(undefined4 *)(param_3 + 0x1f8) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8016,0);
    *(undefined4 *)(param_3 + 0x1fc) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8017,0);
    *(undefined4 *)(param_3 + 0x200) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8019,0);
    *(undefined4 *)(param_3 + 0x204) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x801a,0);
    *(undefined4 *)(param_3 + 0x208) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x801b,0);
    *(undefined4 *)(param_3 + 0x20c) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x801c,0);
    *(undefined4 *)(param_3 + 0x210) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x801d,0);
    *(undefined4 *)(param_3 + 0x214) = uVar10;
    uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x801e,0);
    *(uint *)(param_3 + 0x218) = (uint)uVar7;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8027,0);
    *(undefined4 *)(param_3 + 0x21c) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8028,0);
    *(undefined4 *)(param_3 + 0x220) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8029,0);
    *(undefined4 *)(param_3 + 0x224) = uVar10;
    uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x8022,0);
    *(uint *)(param_3 + 0x238) = (uint)uVar7;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8023,0);
    *(undefined4 *)(param_3 + 0x23c) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8024,0);
    *(undefined4 *)(param_3 + 0x240) = uVar10;
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x802d,0);
    *(undefined4 *)(param_3 + 0x244) = uVar10;
    uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x8026,0);
    *(uint *)(param_3 + 0x248) = (uint)uVar7;
    uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x8021,0);
    *(uint *)(param_3 + 0x24c) = (uint)uVar7;
    uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x8021,1);
    *(uint *)(param_3 + 0x250) = (uint)uVar7;
    uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x8021,2);
    *(uint *)(param_3 + 0x254) = (uint)uVar7;
    uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x8021,3);
    *(uint *)(param_3 + 600) = (uint)uVar7;
    iVar11 = (**(code **)(*param_2 + 0x70))(param_2,0x8021);
    local_d8 = 4;
    if (iVar11 == 8) {
      uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x8021,4);
      *(uint *)(param_3 + 0x25c) = (uint)uVar7;
      uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x8021,5);
      *(uint *)(param_3 + 0x260) = (uint)uVar7;
      uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x8021,6);
      *(uint *)(param_3 + 0x264) = (uint)uVar7;
      uVar7 = (**(code **)(*param_2 + 0x10))(param_2,0x8021,7);
      uVar17 = (uint)uVar7;
    }
    else {
      *(undefined4 *)(param_3 + 0x25c) = *(undefined4 *)(param_3 + 0x254);
      *(undefined4 *)(param_3 + 0x260) = *(undefined4 *)(param_3 + 0x250);
      *(undefined4 *)(param_3 + 0x264) = *(undefined4 *)(param_3 + 0x24c);
      uVar17 = *(uint *)(param_3 + 600);
    }
    *(uint *)(param_3 + 0x268) = uVar17;
    if (param_5 == 0) {
      uVar10 = 0x10000;
    }
    else {
      cVar6 = (**(code **)(*param_2 + 0x80))(param_2,0x9100);
      if (cVar6 == '\0') {
        uVar10 = 0;
      }
      else {
        uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9100,0);
      }
    }
    *(undefined4 *)(param_3 + 8) = uVar10;
    dVar5 = DAT_1404defd8;
    dVar4 = DAT_1404ded88;
    param_3 = param_3 + 0x10;
    sVar20 = -0x7000;
    do {
      iVar11 = (**(code **)(*param_2 + 0x70))(param_2,sVar20);
      bVar2 = false;
      bVar3 = false;
      local_e4 = iVar11;
      FUN_1400d18b0(param_3);
      iVar18 = 0;
      if (0 < iVar11) {
        bVar2 = false;
        bVar3 = false;
        do {
          sVar8 = (**(code **)(*param_2 + 0x10))(param_2,sVar20,iVar18);
          sVar9 = (**(code **)(*param_2 + 0x10))(param_2,sVar20 + 4,iVar18);
          iVar11 = (int)sVar9;
          if (sVar8 == 0) {
            bVar2 = true;
            if (*(LPCRITICAL_SECTION *)(param_3 + 8) != (LPCRITICAL_SECTION)0x0) {
              EnterCriticalSection(*(LPCRITICAL_SECTION *)(param_3 + 8));
            }
            puVar16 = *(undefined8 **)(param_3 + 0x10);
            if ((int)(*(longlong *)(param_3 + 0x18) - (longlong)puVar16 >> 4) != 0) {
              if (local_98 != puVar16) {
                puVar16[1] = (double)iVar11 / dVar5;
                *puVar16 = 0;
              }
              FUN_1400d22d0(param_3);
              *(undefined1 *)(param_3 + 0x48) = 1;
            }
LAB_140169196:
            if (*(LPCRITICAL_SECTION *)(param_3 + 8) != (LPCRITICAL_SECTION)0x0) {
              LeaveCriticalSection(*(LPCRITICAL_SECTION *)(param_3 + 8));
            }
          }
          else if (sVar8 == 0x1ff) {
            bVar3 = true;
            uVar17 = (int)(*(longlong *)(param_3 + 0x18) - *(longlong *)(param_3 + 0x10) >> 4) - 1;
            if (-1 < (int)uVar17) {
              lVar19 = (longlong)(int)uVar17;
              pdVar14 = (double *)(lVar19 * 0x10 + *(longlong *)(param_3 + 0x10));
              do {
                if (*pdVar14 == dVar4) {
                  lpCriticalSection = *(LPCRITICAL_SECTION *)(param_3 + 8);
                  if (sVar9 == 0x1ff) {
                    if (lpCriticalSection != (LPCRITICAL_SECTION)0x0) {
                      EnterCriticalSection(lpCriticalSection);
                    }
                    if (uVar17 < (uint)(*(longlong *)(param_3 + 0x18) -
                                        *(longlong *)(param_3 + 0x10) >> 4)) {
                      puVar16 = (undefined8 *)
                                (*(longlong *)(param_3 + 0x10) + (longlong)(int)uVar17 * 0x10);
                      if (local_b8 != puVar16) {
                        *puVar16 = 0x3ff0000000000000;
                        puVar16[1] = 0x3ff0000000000000;
                      }
                      FUN_1400d22d0(param_3);
                      *(undefined1 *)(param_3 + 0x48) = 1;
                    }
                  }
                  else {
                    if (lpCriticalSection != (LPCRITICAL_SECTION)0x0) {
                      EnterCriticalSection(lpCriticalSection);
                    }
                    if (uVar17 < (uint)(*(longlong *)(param_3 + 0x18) -
                                        *(longlong *)(param_3 + 0x10) >> 4)) {
                      puVar16 = (undefined8 *)
                                (*(longlong *)(param_3 + 0x10) + (longlong)(int)uVar17 * 0x10);
                      if (local_a8 != puVar16) {
                        puVar16[1] = (double)iVar11 / dVar5;
                        *puVar16 = 0x3ff0000000000000;
                      }
                      FUN_1400d22d0(param_3);
                      *(undefined1 *)(param_3 + 0x48) = 1;
                    }
                  }
                  goto LAB_140169196;
                }
                uVar17 = uVar17 - 1;
                pdVar14 = pdVar14 + -2;
                lVar19 = lVar19 + -1;
              } while (-1 < lVar19);
            }
          }
          else {
            local_c8 = (double)(int)sVar8 / dVar5;
            local_c0 = (double)iVar11 / dVar5;
            FUN_1400d1440(param_3,&local_c8);
          }
          iVar18 = iVar18 + 1;
        } while (iVar18 < local_e4);
      }
      iVar11 = (int)(*(longlong *)(param_3 + 0x18) - *(longlong *)(param_3 + 0x10) >> 4) + -1;
      if (-1 < iVar11) {
        lVar19 = (longlong)iVar11 << 4;
        do {
          lVar1 = *(longlong *)(param_3 + 0x10);
          if ((int)(*(longlong *)(param_3 + 0x18) - lVar1 >> 4) < 3) break;
          if (((!bVar2) && (*(double *)(lVar1 + lVar19) == 0.0)) ||
             ((!bVar3 && (*(double *)(lVar1 + lVar19) == dVar4)))) {
            FUN_1400d1810(param_3,iVar11);
          }
          lVar19 = lVar19 + -0x10;
          iVar11 = iVar11 + -1;
        } while (-1 < iVar11);
      }
      param_3 = param_3 + 0x58;
      sVar20 = sVar20 + 1;
      local_d8 = local_d8 + -1;
    } while (local_d8 != 0);
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8030,0);
    uVar12 = (**(code **)(*param_2 + 0x38))(param_2,0x8031,0);
    uVar13 = (**(code **)(*param_2 + 0x38))(param_2,0x8032,0);
    lVar19 = local_d0;
    FUN_1400d2810(local_d0 + 0x170,uVar10,uVar12,uVar13);
    uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x8040,0);
    iVar11 = local_e0;
    *(undefined4 *)(lVar19 + 0x278) = uVar10;
    if (local_e0 < 0x101) {
      *(undefined4 *)(lVar19 + 0x27c) = uVar10;
    }
    else {
      uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x900d,0);
      *(undefined4 *)(lVar19 + 0x27c) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x900e,0);
      *(undefined4 *)(lVar19 + 0x280) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x900f,0);
      *(undefined4 *)(lVar19 + 0x284) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9010,0);
      *(undefined4 *)(lVar19 + 0x288) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9011,0);
      *(undefined4 *)(lVar19 + 0x28c) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9011,1);
      *(undefined4 *)(lVar19 + 0x290) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9011,2);
      *(undefined4 *)(lVar19 + 0x294) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9011,3);
      *(undefined4 *)(lVar19 + 0x298) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9012,0);
      *(undefined4 *)(lVar19 + 0x29c) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9012,1);
      *(undefined4 *)(lVar19 + 0x2a0) = uVar10;
    }
    local_88 = (char *)0x0;
    cVar6 = (**(code **)(*param_2 + 0x48))(param_2,0xd100,&local_88);
    if (((cVar6 == '\0') || (local_88 == (char *)0x0)) || (local_88[0x13] != '\0')) {
      *(undefined1 *)(lVar19 + 0x2cc) = 0;
    }
    else {
      strcpy_s((char *)(lVar19 + 0x2cc),0x14,local_88);
    }
    if (local_88 != (char *)0x0) {
      free(local_88);
    }
    cVar6 = (**(code **)(*param_2 + 0x48))(param_2,0xd101,&local_88);
    if (((cVar6 == '\0') || (local_88 == (char *)0x0)) || (local_88[0x13] != '\0')) {
      *(undefined1 *)(lVar19 + 0x2cc) = 0;
    }
    else {
      strcpy_s((char *)(lVar19 + 0x2e0),0x14,local_88);
    }
    if (0x101 < iVar11) {
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9013,0);
      *(undefined4 *)(lVar19 + 0x2a4) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9014,0);
      *(undefined4 *)(lVar19 + 0x2a8) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9015,0);
      *(undefined4 *)(lVar19 + 0x26c) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9016,0);
      *(undefined4 *)(lVar19 + 0x270) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x9016,1);
      *(undefined4 *)(lVar19 + 0x274) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x9017,0);
      *(undefined4 *)(lVar19 + 0x2b0) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x9018,0);
      *(undefined4 *)(lVar19 + 0x2ac) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x9019,0);
      *(undefined4 *)(lVar19 + 0x2b4) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x38))(param_2,0x901a,0);
      *(undefined4 *)(lVar19 + 0x2b8) = uVar10;
    }
    if (0x102 < iVar11) {
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x901b,0);
      *(undefined4 *)(lVar19 + 700) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x901c,0);
      *(undefined4 *)(lVar19 + 0x2c0) = uVar10;
      uVar10 = (**(code **)(*param_2 + 0x18))(param_2,0x901d,0);
      *(undefined4 *)(lVar19 + 0x2c4) = uVar10;
    }
    if (local_88 != (char *)0x0) {
      free(local_88);
    }
    uVar15 = 0;
  }
  return uVar15;
}



