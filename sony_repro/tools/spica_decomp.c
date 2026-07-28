// 6 functions, roots=0x358be0 0x39da60 0x359390 0x3599a0 0x35a1d0 0x35a600 depth=0

// ===== depth0 FUN_140358be0 @ 0x140358be0 rva=0x358be0 size=1961 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */
/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

undefined8 * FUN_140358be0(longlong *param_1,longlong param_2,longlong *param_3)

{
  int iVar1;
  longlong *plVar2;
  float fVar3;
  float fVar4;
  longlong *plVar5;
  char cVar6;
  undefined2 uVar7;
  short sVar8;
  int iVar9;
  undefined4 uVar10;
  int iVar11;
  undefined8 *puVar12;
  undefined8 uVar13;
  longlong lVar14;
  undefined8 *puVar15;
  uint uVar16;
  undefined2 uVar17;
  longlong lVar18;
  longlong lVar19;
  uint uVar20;
  uint uVar22;
  longlong lVar23;
  longlong lVar24;
  undefined4 uVar25;
  float fVar26;
  float fVar27;
  float fVar28;
  float fVar29;
  float fVar30;
  float fVar31;
  float fVar32;
  undefined1 auStack_178 [32];
  uint local_158;
  uint local_150;
  longlong local_148;
  longlong local_140;
  uint local_138;
  longlong *local_130;
  longlong *local_128;
  undefined8 *local_120;
  longlong local_118;
  longlong *local_110;
  undefined **local_108;
  int local_100;
  int local_fc;
  int local_f8;
  int local_f4;
  int local_f0;
  int local_ec;
  ulonglong local_e8;
  ulonglong uVar21;
  
  plVar5 = DAT_1405a8b18;
  local_e8 = DAT_140559440 ^ (ulonglong)auStack_178;
  local_128 = DAT_1405a8b18;
  plVar2 = (longlong *)*param_3;
  if (plVar2 == (longlong *)0x0) {
    puVar12 = (undefined8 *)0x1;
  }
  else {
    local_130 = param_1;
    local_118 = param_2;
    local_110 = param_3;
    cVar6 = (**(code **)(*plVar2 + 0xf8))(plVar2);
    uVar25 = 1;
    if (cVar6 == '\0') {
      cVar6 = (**(code **)(*plVar2 + 0xe8))(plVar2);
      if (cVar6 == '\0') {
        uVar25 = 0x11;
        if (*(int *)((longlong)plVar2 + 0x144) == 3) {
          uVar25 = 1;
        }
      }
      else {
        iVar9 = (**(code **)(*plVar2 + 0x100))(plVar2);
        uVar25 = 4;
        if (iVar9 != 0x10) {
          uVar25 = 2;
        }
      }
    }
    FUN_140153c90(*plVar5,uVar25);
    uVar13 = FUN_140153c50(uVar25);
    lVar19 = *plVar5;
    uVar10 = (**(code **)(*plVar2 + 0xe0))(plVar2);
    FUN_14035a600(lVar19,uVar13,uVar10);
    iVar11 = (**(code **)(*plVar2 + 0xe0))(plVar2);
    lVar14 = FUN_140153c50(uVar25);
    lVar19 = local_118;
    fVar26 = DAT_1404debbc;
    iVar9 = *(int *)(lVar14 + 0xc0);
    if (iVar11 < iVar9) {
      fVar28 = *(float *)(lVar14 + 0xcc);
    }
    else {
      iVar1 = *(int *)(lVar14 + 0xc4);
      if (iVar11 < iVar1) {
        fVar28 = (float)(iVar11 - iVar9) / (float)(iVar1 - iVar9);
        fVar28 = (DAT_1404debbc - fVar28) * *(float *)(lVar14 + 0xcc) +
                 fVar28 * *(float *)(lVar14 + 0xd0);
      }
      else if (iVar11 < *(int *)(lVar14 + 200)) {
        fVar28 = (float)(iVar11 - iVar1) / (float)(*(int *)(lVar14 + 200) - iVar1);
        fVar28 = (DAT_1404debbc - fVar28) * *(float *)(lVar14 + 0xd0) +
                 fVar28 * *(float *)(lVar14 + 0xd4);
      }
      else {
        fVar28 = *(float *)(lVar14 + 0xd4);
      }
    }
    lVar14 = FUN_140152710(*(undefined8 *)(local_118 + 8),0);
    local_108 = sony_zhacai::ZcRectT<int>::vftable;
    local_100 = *(int *)(lVar19 + 0x30);
    local_fc = *(int *)(lVar19 + 0x34);
    local_f8 = *(int *)(lVar19 + 0x38);
    local_f4 = *(int *)(lVar19 + 0x3c);
    local_120 = operator_new(0x38);
    puVar12 = (undefined8 *)0x0;
    puVar15 = puVar12;
    if (local_120 != (undefined8 *)0x0) {
      *local_120 = 0;
      local_120[1] = 0;
      local_120[2] = 0;
      local_120[3] = 0;
      local_120[4] = 0;
      local_120[5] = 0;
      local_120[6] = 0;
      puVar15 = (undefined8 *)FUN_140152c00(local_120);
    }
    iVar9 = FUN_140152cb0(puVar15,local_f8,local_f4);
    fVar4 = DAT_1404df2cc;
    fVar3 = DAT_1404df248;
    fVar29 = DAT_1404667a4;
    fVar30 = DAT_140466790;
    if (iVar9 == 0) {
      local_138 = local_fc + 3;
      uVar7 = 0;
      iVar9 = local_f4;
      iVar11 = local_f8;
      if (local_138 < local_f4 - 3U) {
        do {
          uVar20 = local_100 + 3;
          if (uVar20 < iVar11 - 3U) {
            do {
              plVar2 = local_128;
              uVar16 = local_138;
              uVar21 = (ulonglong)uVar20;
              local_148 = *local_128;
              local_158 = uVar20;
              local_150 = local_138;
              sVar8 = (**(code **)(*param_1 + 0x58))(param_1,lVar14,&local_ec,&local_f0);
              local_120 = (undefined8 *)CONCAT44(local_120._4_4_,local_f0 - local_ec);
              lVar24 = (longlong)sVar8;
              lVar19 = *(longlong *)(*plVar2 + 0x28c8);
              local_158 = *(uint *)(lVar19 + lVar24 * 8);
              uVar22 = (uint)*(ushort *)
                              ((ulonglong)(uVar16 * *(int *)(lVar14 + 0x14)) +
                               *(longlong *)(lVar14 + 0x20) + uVar21 * 2);
              local_140 = (ulonglong)local_158 * 0x68 + *(longlong *)(*plVar2 + 0x28d0);
              local_150 = (uint)*(char *)(lVar19 + 4 + lVar24 * 8);
              local_148 = CONCAT44(local_148._4_4_,(int)*(char *)(lVar19 + 5 + lVar24 * 8));
              fVar26 = (float)(**(code **)(*local_130 + 0x60))(local_130,lVar14,uVar21,uVar16);
              lVar19 = *local_128;
              uVar17 = uVar7;
              if (*(int *)(lVar19 + 0x38) == 0) {
                iVar9 = (int)fVar26 >> 9;
                if (-1 < iVar9) {
                  if (0x3fff < iVar9) {
                    iVar9 = 0x3fff;
                  }
                  uVar17 = (short)iVar9;
                }
              }
              else {
                fVar32 = (fVar26 - (float)(uVar22 << 9)) * fVar28;
                fVar31 = fVar32 * DAT_140466798;
                fVar26 = fVar3;
                if (*(char *)(lVar19 + 0x3c) != '\0') {
                  fVar26 = fVar31;
                  if (fVar31 <= 0.0) {
                    fVar26 = fVar31 * fVar4;
                  }
                  local_150 = *(undefined4 *)(lVar19 + 0xb8);
                  local_158 = *(undefined4 *)(lVar19 + 0xb4);
                  local_148._0_4_ = (float)((local_ec + local_f0) / 2);
                  fVar27 = (float)FUN_14035a1d0();
                  lVar24 = 0x58;
                  if (fVar31 <= 0.0) {
                    lVar24 = 0x78;
                  }
                  lVar23 = 0x54;
                  if (fVar31 <= 0.0) {
                    lVar23 = 0x74;
                  }
                  lVar18 = 0x4c;
                  if (fVar31 <= 0.0) {
                    lVar18 = 0x6c;
                  }
                  uVar13 = 0x48;
                  if (fVar31 <= 0.0) {
                    uVar13 = 0x68;
                  }
                  local_150 = *(undefined4 *)(lVar24 + lVar19);
                  local_158 = *(undefined4 *)(lVar23 + lVar19);
                  local_148._0_4_ = fVar26;
                  fVar26 = (float)FUN_14035a1d0(uVar13,lVar18,*(undefined4 *)(lVar18 + lVar19));
                  local_148 = CONCAT44(local_148._4_4_,(float)(int)local_120);
                  local_150 = *(int *)(lVar19 + 0x98);
                  local_158 = *(undefined4 *)(lVar19 + 0x94);
                  fVar31 = (float)FUN_14035a1d0();
                  if (fVar31 <= fVar26) {
                    fVar26 = fVar31;
                  }
                  if (fVar27 <= fVar26) {
                    fVar26 = fVar27;
                  }
                  fVar26 = fVar26 * *(float *)(lVar19 + 0xc4) * fVar30;
                }
                iVar9 = uVar22 - (int)(fVar26 * fVar29 * fVar32 * DAT_140466794);
                if (-1 < iVar9) {
                  if (0x3fff < iVar9) {
                    iVar9 = 0x3fff;
                  }
                  uVar17 = (short)iVar9;
                }
              }
              *(undefined2 *)
               ((ulonglong)(local_138 * *(int *)((longlong)puVar15 + 0x14)) + puVar15[4] +
               uVar21 * 2) = uVar17;
              uVar20 = uVar20 + 1;
              param_1 = local_130;
              iVar9 = local_f4;
              iVar11 = local_f8;
            } while (uVar20 < local_f8 - 3U);
          }
          local_138 = local_138 + 1;
          lVar19 = local_118;
          fVar26 = DAT_1404debbc;
        } while (local_138 < iVar9 - 3U);
      }
      fVar28 = DAT_1404667a0;
      fVar30 = ((float)(100 - *(int *)(local_110[1] + 0x2c4)) / _DAT_14046679c) *
               ((float)(*(int *)(local_110[1] + 0x208) + 100) / DAT_1404df210) *
               *(float *)(*local_128 + 0xc);
      uVar20 = local_fc + 3;
      if (uVar20 < iVar9 - 3U) {
        uVar16 = iVar11 - 3;
        do {
          uVar22 = local_100 + 3;
          if (uVar22 < uVar16) {
            do {
              lVar24 = (ulonglong)(uVar20 * *(int *)(lVar14 + 0x14)) + (ulonglong)uVar22 * 2;
              fVar29 = (float)(int)*(short *)((ulonglong)
                                              (uVar20 * *(int *)((longlong)puVar15 + 0x14)) +
                                              (ulonglong)uVar22 * 2 + puVar15[4]) * fVar30 +
                       (float)*(ushort *)(lVar24 + *(longlong *)(lVar14 + 0x20)) * (fVar26 - fVar30)
              ;
              uVar17 = uVar7;
              if ((0.0 <= fVar29) && (uVar17 = 0x7fff, fVar29 <= fVar28)) {
                uVar17 = (short)(int)fVar29;
              }
              *(undefined2 *)(lVar24 + *(longlong *)(lVar14 + 0x20)) = uVar17;
              uVar22 = uVar22 + 1;
              uVar16 = local_f8 - 3;
              iVar9 = local_f4;
            } while (uVar22 < uVar16);
          }
          uVar20 = uVar20 + 1;
        } while (uVar20 < iVar9 - 3U);
      }
      local_158 = 4;
      (*(code *)local_108[0xb])(&local_108,4,4,4);
      *(int *)(lVar19 + 0x30) = local_100;
      *(int *)(lVar19 + 0x38) = local_f8;
      *(int *)(lVar19 + 0x34) = local_fc;
      *(int *)(lVar19 + 0x3c) = local_f4;
      if (puVar15 != (undefined8 *)0x0) {
        (**(code **)*puVar15)(puVar15,1);
      }
    }
    else {
      if (puVar15 != (undefined8 *)0x0) {
        (**(code **)*puVar15)(puVar15,1);
      }
      puVar12 = (undefined8 *)0x6;
    }
  }
  return puVar12;
}



// ===== depth0 FUN_14039da60 @ 0x14039da60 rva=0x39da60 size=3282 =====

/* WARNING: Function: __security_check_cookie replaced with injection: security_check_cookie */
/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

undefined8 * FUN_14039da60(longlong param_1,longlong param_2,longlong *param_3)

{
  longlong lVar1;
  longlong lVar2;
  float fVar3;
  undefined1 auVar4 [14];
  undefined1 auVar5 [14];
  undefined1 auVar6 [14];
  undefined1 auVar7 [14];
  undefined1 auVar8 [14];
  char cVar9;
  undefined2 uVar10;
  int iVar11;
  undefined4 uVar12;
  uint uVar13;
  longlong *plVar14;
  undefined8 *puVar15;
  undefined8 uVar16;
  longlong lVar17;
  undefined8 *puVar18;
  int iVar19;
  int iVar20;
  longlong lVar21;
  uint uVar22;
  uint uVar24;
  uint uVar25;
  uint uVar26;
  undefined4 uVar27;
  float fVar28;
  undefined1 auVar29 [32];
  undefined1 extraout_var [56];
  float fVar30;
  undefined1 auVar31 [32];
  undefined1 auVar32 [32];
  undefined1 auVar33 [32];
  undefined1 auVar34 [32];
  undefined1 auVar35 [16];
  undefined1 auVar36 [32];
  undefined1 auVar37 [32];
  ushort uVar38;
  ushort uVar39;
  ushort uVar40;
  ushort uVar41;
  ushort uVar42;
  undefined1 auVar43 [32];
  undefined1 auVar44 [32];
  undefined1 auVar45 [16];
  undefined1 auVar46 [16];
  float fVar47;
  undefined1 auVar48 [16];
  undefined1 auVar49 [16];
  undefined1 auVar53 [16];
  undefined1 in_ZMM9 [64];
  undefined1 auVar54 [64];
  undefined1 in_ZMM10 [64];
  undefined1 auVar55 [64];
  undefined1 in_ZMM11 [64];
  undefined1 in_ZMM12 [64];
  undefined1 auVar56 [64];
  undefined1 auStack_278 [32];
  longlong local_258;
  longlong local_250;
  longlong local_248;
  longlong *local_240;
  uint local_238;
  uint local_230;
  uint local_228;
  int local_220;
  int local_218;
  longlong local_210;
  uint local_200;
  uint local_1fc;
  uint local_1f8;
  uint local_1f4;
  longlong *local_1f0;
  longlong local_1e8;
  longlong local_1e0;
  longlong local_1d8;
  longlong local_1d0;
  longlong *local_1c8;
  uint local_1c0;
  undefined8 *local_1b8;
  longlong local_1b0;
  longlong local_1a8;
  longlong local_1a0;
  longlong local_198;
  longlong local_190;
  longlong local_188;
  undefined8 *local_180;
  longlong local_178;
  longlong local_170;
  longlong *local_168;
  longlong local_160;
  undefined1 local_140 [32];
  undefined1 local_120 [32];
  undefined **local_100;
  int local_f8;
  int local_f4;
  int local_f0;
  int local_ec;
  float local_e8 [2];
  longlong local_e0;
  longlong local_d8;
  longlong local_d0;
  ulonglong local_c8;
  undefined1 local_a8 [16];
  undefined1 local_98 [16];
  undefined1 local_88 [16];
  undefined1 local_78 [16];
  ulonglong uVar23;
  undefined1 auVar50 [16];
  undefined1 auVar51 [16];
  undefined1 auVar52 [16];
  
  local_78 = in_ZMM9._0_16_;
  local_88 = in_ZMM10._0_16_;
  local_98 = in_ZMM11._0_16_;
  local_a8 = in_ZMM12._0_16_;
  local_c8 = DAT_140559440 ^ (ulonglong)auStack_278;
  local_1d8 = param_1;
  local_168 = param_3;
  local_160 = param_2;
  plVar14 = (longlong *)FUN_140359d00();
  param_3 = (longlong *)*param_3;
  if (param_3 == (longlong *)0x0) {
    puVar15 = (undefined8 *)0x1;
  }
  else {
    local_1f0 = plVar14;
    cVar9 = (**(code **)(*param_3 + 0xf8))(param_3);
    uVar27 = 1;
    if (cVar9 == '\0') {
      cVar9 = (**(code **)(*param_3 + 0xe8))(param_3);
      if (cVar9 == '\0') {
        uVar27 = 0x11;
        if (*(int *)((longlong)param_3 + 0x144) == 3) {
          uVar27 = 1;
        }
      }
      else {
        iVar11 = (**(code **)(*param_3 + 0x100))(param_3);
        uVar27 = 4;
        if (iVar11 != 0x10) {
          uVar27 = 2;
        }
      }
    }
    FUN_140153c90(*plVar14,uVar27);
    uVar16 = FUN_140153c50(uVar27);
    lVar17 = *plVar14;
    uVar12 = (**(code **)(*param_3 + 0xe0))(param_3);
    FUN_14035a600(lVar17,uVar16,uVar12);
    uVar12 = (**(code **)(*param_3 + 0xe0))(param_3);
    uVar16 = FUN_14035a450(uVar12,uVar27);
    auVar56._8_4_ = extraout_var._0_4_;
    auVar56._0_8_ = uVar16;
    auVar56._16_48_ = in_ZMM11._16_48_;
    auVar56._12_4_ = extraout_var._4_4_;
    lVar17 = FUN_140152710(*(undefined8 *)(param_2 + 8),0);
    local_100 = sony_zhacai::ZcRectT<int>::vftable;
    local_f8 = *(int *)(param_2 + 0x30);
    local_f4 = *(int *)(param_2 + 0x34);
    local_f0 = *(int *)(param_2 + 0x38);
    local_ec = *(int *)(param_2 + 0x3c);
    local_188 = lVar17;
    local_180 = operator_new(0x38);
    puVar15 = (undefined8 *)0x0;
    puVar18 = puVar15;
    if (local_180 != (undefined8 *)0x0) {
      *local_180 = 0;
      local_180[1] = 0;
      local_180[2] = 0;
      local_180[3] = 0;
      local_180[4] = 0;
      local_180[5] = 0;
      local_180[6] = 0;
      puVar18 = (undefined8 *)FUN_140152c00(local_180);
    }
    local_1b8 = puVar18;
    iVar11 = FUN_140152cb0(puVar18,local_f0,local_ec);
    fVar3 = DAT_140466798;
    if (iVar11 == 0) {
      local_1b0 = *local_1f0;
      lVar1 = *(longlong *)(local_1b0 + 0x28c8);
      lVar2 = *(longlong *)(lVar17 + 0x20);
      local_190 = puVar18[4];
      local_200 = *(uint *)(lVar17 + 0x14);
      local_1f8 = *(uint *)((longlong)puVar18 + 0x14);
      iVar11 = local_f0;
      if (*(int *)(local_1b0 + 0x38) == 0) {
        uVar24 = local_f4 + 3;
        if (uVar24 < local_ec - 3U) {
          uVar25 = local_200 >> 1;
          local_1f4 = local_1f8 >> 1;
          uVar13 = local_f0 - 3;
          local_e8[0] = (float)(local_f4 + 5);
          plVar14 = local_1f0;
          local_1fc = uVar25;
          do {
            local_1d0 = lVar2 + (ulonglong)((uVar24 - 3) * uVar25) * 2;
            local_1e0 = lVar2 + (ulonglong)((uVar24 - 2) * uVar25) * 2;
            local_e0 = lVar2 + (ulonglong)((uVar24 - 1) * uVar25) * 2;
            local_1e8 = lVar2 + (ulonglong)(uVar25 * uVar24) * 2;
            local_d8 = lVar2 + (ulonglong)(((int)local_e8[0] + -1) * uVar25) * 2;
            local_d0 = lVar2 + (ulonglong)((int)local_e8[0] * uVar25) * 2;
            local_e8[0] = (float)((int)local_e8[0] + 1);
            local_1c8 = (longlong *)(lVar2 + (ulonglong)((int)local_e8[0] * uVar25) * 2);
            local_1d8 = local_190 + (ulonglong)(local_1f4 * uVar24) * 2;
            uVar22 = local_f8 + 3;
            if (uVar22 < uVar13) {
              do {
                uVar23 = (ulonglong)uVar22;
                auVar45._10_2_ = 0;
                auVar45._8_2_ = *(ushort *)(local_1e8 + (ulonglong)(uVar22 - 2) * 2);
                auVar45._12_2_ = *(undefined2 *)(local_1e8 + (ulonglong)(uVar22 - 1) * 2);
                auVar45._2_2_ = 0;
                auVar45._0_2_ = *(ushort *)(local_1e0 + uVar23 * 2);
                auVar45._4_2_ = *(undefined2 *)(local_e0 + uVar23 * 2);
                auVar45._6_2_ = 0;
                auVar45._14_2_ = 0;
                auVar35 = *(undefined1 (*) [16])(local_1e8 + uVar23 * 2);
                auVar4._10_2_ = 0;
                auVar4._0_10_ = auVar35._0_10_;
                auVar4._12_2_ = auVar35._6_2_;
                auVar5._8_2_ = auVar35._4_2_;
                auVar5._0_8_ = auVar35._0_8_;
                auVar5._10_4_ = auVar4._10_4_;
                auVar8._6_8_ = 0;
                auVar8._0_6_ = auVar5._8_6_;
                auVar6._4_2_ = auVar35._2_2_;
                auVar6._0_4_ = auVar35._0_4_;
                auVar6._6_8_ = SUB148(auVar8 << 0x40,6);
                auVar7._4_10_ = auVar6._4_10_;
                auVar7._0_4_ = auVar35._0_4_ & 0xffff;
                auVar48._0_8_ = auVar7._0_8_;
                auVar48._8_8_ =
                     (ulonglong)auVar5._8_6_ & 0xffffffff |
                     (ulonglong)*(ushort *)(local_d8 + uVar23 * 2) << 0x20;
                auVar35 = packusdw(auVar45,auVar48);
                uVar39 = auVar35._8_2_;
                uVar38 = auVar35._0_2_;
                uVar38 = (uVar38 < uVar39) * uVar39 | (uVar38 >= uVar39) * uVar38;
                uVar39 = auVar35._10_2_;
                uVar40 = auVar35._2_2_;
                uVar40 = (uVar40 < uVar39) * uVar39 | (uVar40 >= uVar39) * uVar40;
                uVar39 = auVar35._12_2_;
                uVar41 = auVar35._4_2_;
                uVar41 = (uVar41 < uVar39) * uVar39 | (uVar41 >= uVar39) * uVar41;
                uVar39 = auVar35._14_2_;
                uVar42 = auVar35._6_2_;
                uVar42 = (uVar42 < uVar39) * uVar39 | (uVar42 >= uVar39) * uVar42;
                uVar39 = (uVar38 < uVar41) * uVar41 | (uVar38 >= uVar41) * uVar38;
                uVar38 = (uVar40 < uVar42) * uVar42 | (uVar40 >= uVar42) * uVar40;
                auVar35 = phminposuw(auVar35);
                uVar13 = auVar35._0_4_ & 0xffff;
                uVar25 = (uint)(ushort)((uVar39 < uVar38) * uVar38 | (uVar39 >= uVar38) * uVar39);
                uVar39 = *(ushort *)(local_d0 + uVar23 * 2);
                if (uVar39 <= uVar13) {
                  uVar13 = (uint)uVar39;
                }
                uVar26 = (uint)uVar39;
                if (uVar25 < uVar26) {
                  uVar25 = uVar26;
                }
                iVar11 = (int)(uVar25 - uVar13) / 2;
                puVar18 = puVar15;
                if ((uint)(longlong)*(float *)(local_1b0 + 8) <= uVar25 - uVar13) {
                  auVar35 = ZEXT416((uint)(float)iVar11);
                  auVar35 = vshufps_avx(auVar35,auVar35,0);
                  auVar43._16_16_ = auVar35;
                  auVar43._0_16_ = auVar35;
                  auVar31._16_16_ = auVar48;
                  auVar31._0_16_ = auVar45;
                  auVar32 = vcvtdq2ps_avx(auVar31);
                  auVar35 = vcvtdq2ps_avx(ZEXT416(uVar13));
                  auVar35 = vshufps_avx(auVar35,auVar35,0);
                  auVar36._16_16_ = auVar35;
                  auVar36._0_16_ = auVar35;
                  auVar32 = vsubps_avx(auVar32,auVar36);
                  auVar32 = vcmpps_avx(auVar32,auVar43,0xd);
                  iVar19 = vmovmskps_avx(auVar32);
                  iVar20 = 0;
                  if (iVar11 <= (int)(uVar26 - uVar13)) {
                    iVar20 = 0x100;
                  }
                  uVar13 = iVar20 + iVar19;
                  puVar18 = (undefined8 *)(ulonglong)uVar13;
                  if ((uVar13 & 0x100) != 0) {
                    puVar18 = (undefined8 *)(ulonglong)(~uVar13 & 0xff);
                  }
                }
                lVar17 = (longlong)(short)puVar18;
                uVar13 = *(uint *)(lVar1 + lVar17 * 8);
                iVar19 = (int)*(char *)(lVar1 + 4 + lVar17 * 8);
                iVar20 = (int)*(char *)(lVar1 + 5 + lVar17 * 8);
                iVar11 = -iVar19;
                if (-iVar19 < 0) {
                  iVar11 = iVar19;
                }
                if (iVar11 == 1) {
                  iVar11 = -iVar20;
                  if (-iVar20 < 0) {
                    iVar11 = iVar20;
                  }
                  if (iVar11 != 1) goto LAB_14039df62;
                  local_210 = (ulonglong)uVar13 * 0x68 + *(longlong *)(*plVar14 + 0x28d0);
                  local_240 = local_1c8;
                  local_248 = local_d0;
                  local_250 = local_d8;
                  local_258 = local_1e8;
                  local_238 = uVar22;
                  local_230 = uVar24;
                  local_228 = uVar13;
                  local_220 = iVar19;
                  local_218 = iVar20;
                  FUN_14039ead0(param_1,local_1d0,local_1e0,local_e0);
                }
                else {
LAB_14039df62:
                  local_240 = (longlong *)
                              ((ulonglong)uVar13 * 0x68 + *(longlong *)(*plVar14 + 0x28d0));
                  local_238 = local_200;
                  local_248 = CONCAT44(local_248._4_4_,iVar20);
                  local_250 = CONCAT44(local_250._4_4_,iVar19);
                  local_258 = CONCAT44(local_258._4_4_,uVar13);
                  FUN_14039e740(param_1,lVar2,uVar23,uVar24);
                }
                uVar10 = FUN_14035a250();
                *(undefined2 *)(local_1d8 + uVar23 * 2) = uVar10;
                uVar22 = uVar22 + 1;
                uVar13 = local_f0 - 3;
                plVar14 = local_1f0;
                uVar25 = local_1fc;
                iVar11 = local_f0;
              } while (uVar22 < uVar13);
            }
            uVar24 = uVar24 + 1;
            puVar18 = local_1b8;
            lVar17 = local_188;
          } while (uVar24 < local_ec - 3U);
        }
      }
      else {
        uVar24 = local_f4 + 3;
        if (uVar24 < local_ec - 3U) {
          uVar13 = local_200 >> 1;
          local_1e8 = CONCAT44(local_1e8._4_4_,uVar13);
          uVar25 = local_1f8 >> 1;
          local_1e0 = CONCAT44(local_1e0._4_4_,uVar25);
          local_1fc = local_f4 + 5;
          auVar54._16_48_ = in_ZMM9._16_48_;
          auVar54._0_16_ = ZEXT416(DAT_140466794);
          auVar55._16_48_ = in_ZMM10._16_48_;
          auVar55._0_16_ = ZEXT416(DAT_1404667a4);
          plVar14 = local_1f0;
          do {
            local_170 = lVar2 + (ulonglong)((uVar24 - 3) * uVar13) * 2;
            local_1c8 = (longlong *)(lVar2 + (ulonglong)((uVar24 - 2) * uVar13) * 2);
            local_198 = lVar2 + (ulonglong)((uVar24 - 1) * uVar13) * 2;
            local_1d0 = lVar2 + (ulonglong)(uVar13 * uVar24) * 2;
            local_1a0 = lVar2 + (ulonglong)((local_1fc - 1) * uVar13) * 2;
            local_1a8 = lVar2 + (ulonglong)(local_1fc * uVar13) * 2;
            local_178 = lVar2 + (ulonglong)((local_1fc + 1) * uVar13) * 2;
            local_180 = (undefined8 *)(local_190 + (ulonglong)(uVar25 * uVar24) * 2);
            uVar22 = local_f8 + 3;
            if (uVar22 < iVar11 - 3U) {
              in_ZMM12._0_16_ = ZEXT816(0);
              do {
                uVar23 = (ulonglong)uVar22;
                auVar46._10_2_ = 0;
                auVar46._8_2_ = *(ushort *)(local_1d0 + (ulonglong)(uVar22 - 2) * 2);
                auVar46._12_2_ = *(undefined2 *)(local_1d0 + (ulonglong)(uVar22 - 1) * 2);
                auVar46._2_2_ = 0;
                auVar46._0_2_ = *(ushort *)((longlong)local_1c8 + uVar23 * 2);
                auVar46._4_2_ = *(undefined2 *)(local_198 + uVar23 * 2);
                auVar46._6_2_ = 0;
                auVar46._14_2_ = 0;
                auVar35 = *(undefined1 (*) [16])(local_1d0 + uVar23 * 2);
                auVar52._0_12_ = auVar35._0_12_;
                auVar52._12_2_ = auVar35._6_2_;
                auVar52._14_2_ = in_ZMM12._6_2_;
                auVar51._12_4_ = auVar52._12_4_;
                auVar51._0_10_ = auVar35._0_10_;
                auVar51._10_2_ = in_ZMM12._4_2_;
                auVar50._10_6_ = auVar51._10_6_;
                auVar50._0_8_ = auVar35._0_8_;
                auVar50._8_2_ = auVar35._4_2_;
                auVar49._8_8_ = auVar50._8_8_;
                auVar49._6_2_ = in_ZMM12._2_2_;
                auVar49._4_2_ = auVar35._2_2_;
                auVar49._0_2_ = auVar35._0_2_;
                auVar49._2_2_ = in_ZMM12._0_2_;
                auVar53._0_8_ = auVar49._0_8_;
                auVar53._8_8_ =
                     auVar49._8_8_ & 0xffffffff |
                     (ulonglong)*(ushort *)(local_1a0 + uVar23 * 2) << 0x20;
                auVar35 = packusdw(auVar46,auVar53);
                uVar39 = auVar35._8_2_;
                uVar38 = auVar35._0_2_;
                uVar38 = (uVar38 < uVar39) * uVar39 | (uVar38 >= uVar39) * uVar38;
                uVar39 = auVar35._10_2_;
                uVar40 = auVar35._2_2_;
                uVar40 = (uVar40 < uVar39) * uVar39 | (uVar40 >= uVar39) * uVar40;
                uVar39 = auVar35._12_2_;
                uVar41 = auVar35._4_2_;
                uVar41 = (uVar41 < uVar39) * uVar39 | (uVar41 >= uVar39) * uVar41;
                uVar39 = auVar35._14_2_;
                uVar42 = auVar35._6_2_;
                uVar42 = (uVar42 < uVar39) * uVar39 | (uVar42 >= uVar39) * uVar42;
                uVar39 = (uVar38 < uVar41) * uVar41 | (uVar38 >= uVar41) * uVar38;
                uVar38 = (uVar40 < uVar42) * uVar42 | (uVar40 >= uVar42) * uVar40;
                auVar35 = phminposuw(auVar35);
                local_1f4 = auVar35._0_4_ & 0xffff;
                local_1c0 = (uint)(ushort)((uVar39 < uVar38) * uVar38 | (uVar39 >= uVar38) * uVar39)
                ;
                uVar39 = *(ushort *)(local_1a8 + uVar23 * 2);
                if (uVar39 <= local_1f4) {
                  local_1f4 = (uint)uVar39;
                }
                uVar13 = (uint)uVar39;
                if (local_1c0 < uVar13) {
                  local_1c0 = uVar13;
                }
                iVar11 = (int)(local_1c0 - local_1f4) / 2;
                puVar18 = puVar15;
                if ((uint)(longlong)*(float *)(local_1b0 + 8) <= local_1c0 - local_1f4) {
                  auVar35 = ZEXT416((uint)(float)iVar11);
                  auVar35 = vshufps_avx(auVar35,auVar35,0);
                  auVar44._16_16_ = auVar35;
                  auVar44._0_16_ = auVar35;
                  auVar32._16_16_ = auVar53;
                  auVar32._0_16_ = auVar46;
                  auVar32 = vcvtdq2ps_avx(auVar32);
                  auVar35 = vcvtdq2ps_avx(ZEXT416(local_1f4));
                  auVar35 = vshufps_avx(auVar35,auVar35,0);
                  auVar37._16_16_ = auVar35;
                  auVar37._0_16_ = auVar35;
                  auVar32 = vsubps_avx(auVar32,auVar37);
                  auVar32 = vcmpps_avx(auVar32,auVar44,0xd);
                  iVar19 = vmovmskps_avx(auVar32);
                  iVar20 = 0;
                  if (iVar11 <= (int)(uVar13 - local_1f4)) {
                    iVar20 = 0x100;
                  }
                  uVar13 = iVar20 + iVar19;
                  puVar18 = (undefined8 *)(ulonglong)uVar13;
                  auVar54 = ZEXT1664(auVar54._0_16_);
                  auVar55 = ZEXT1664(auVar55._0_16_);
                  auVar56 = ZEXT1664(auVar56._0_16_);
                  in_ZMM12 = ZEXT1664(in_ZMM12._0_16_);
                  if ((uVar13 & 0x100) != 0) {
                    puVar18 = (undefined8 *)(ulonglong)(~uVar13 & 0xff);
                  }
                }
                lVar17 = (longlong)(short)puVar18;
                uVar13 = *(uint *)(lVar1 + lVar17 * 8);
                iVar19 = (int)*(char *)(lVar1 + 4 + lVar17 * 8);
                iVar20 = (int)*(char *)(lVar1 + 5 + lVar17 * 8);
                uVar25 = (uint)*(ushort *)(local_1d0 + uVar23 * 2);
                iVar11 = -iVar19;
                if (-iVar19 < 0) {
                  iVar11 = iVar19;
                }
                if (iVar11 == 1) {
                  iVar11 = -iVar20;
                  if (-iVar20 < 0) {
                    iVar11 = iVar20;
                  }
                  if (iVar11 != 1) goto LAB_14039e2c8;
                  local_210 = (ulonglong)uVar13 * 0x68 + *(longlong *)(*plVar14 + 0x28d0);
                  local_240 = (longlong *)local_178;
                  local_248 = local_1a8;
                  local_250 = local_1a0;
                  local_258 = local_1d0;
                  local_238 = uVar22;
                  local_230 = uVar24;
                  local_228 = uVar13;
                  local_220 = iVar19;
                  local_218 = iVar20;
                  fVar28 = (float)FUN_14039ead0(local_1d8,local_170,local_1c8,local_198);
                }
                else {
LAB_14039e2c8:
                  local_240 = (longlong *)
                              ((ulonglong)uVar13 * 0x68 + *(longlong *)(*plVar14 + 0x28d0));
                  local_238 = local_200;
                  local_248 = CONCAT44(local_248._4_4_,iVar20);
                  local_250 = CONCAT44(local_250._4_4_,iVar19);
                  local_258 = CONCAT44(local_258._4_4_,uVar13);
                  fVar28 = (float)FUN_14039e740(local_1d8,lVar2,uVar23,uVar24);
                }
                plVar14 = local_1f0;
                fVar28 = (fVar28 - (float)(uVar25 << 9)) * auVar56._0_4_;
                local_240 = local_1f0;
                local_248 = CONCAT44(local_248._4_4_,(float)((int)(local_1c0 + local_1f4) / 2));
                local_250 = CONCAT44(local_250._4_4_,(float)(int)(local_1c0 - local_1f4));
                local_258 = CONCAT44(local_258._4_4_,fVar28 * fVar3);
                FUN_14035a270(local_e8,&local_d0,&local_d8,&local_e0);
                local_e8[0] = local_e8[0] * auVar54._0_4_;
                iVar11 = uVar25 - (int)(local_e8[0] * fVar28 * auVar55._0_4_);
                if (iVar11 < 0) {
                  iVar11 = 0;
                }
                else if (0x3fff < iVar11) {
                  iVar11 = 0x3fff;
                }
                *(short *)((longlong)local_180 + uVar23 * 2) = (short)iVar11;
                uVar22 = uVar22 + 1;
              } while (uVar22 < local_f0 - 3U);
              iVar11 = local_f0;
              uVar13 = (uint)local_1e8;
              uVar25 = (uint)local_1e0;
            }
            uVar24 = uVar24 + 1;
            local_1fc = local_1fc + 1;
            puVar18 = local_1b8;
            lVar17 = local_188;
          } while (uVar24 < local_ec - 3U);
        }
      }
      fVar28 = DAT_1404667a0;
      fVar47 = ((float)(100 - *(int *)(local_168[1] + 0x2c4)) / _DAT_14046679c) *
               ((float)(*(int *)(local_168[1] + 0x208) + 100) / DAT_1404df210) *
               *(float *)(local_1b0 + 0xc);
      lVar1 = *(longlong *)(lVar17 + 0x20);
      lVar2 = puVar18[4];
      fVar3 = DAT_1404debbc - fVar47;
      auVar35 = vshufps_avx(ZEXT416((uint)fVar3),ZEXT416((uint)fVar3),0);
      local_120._16_16_ = auVar35;
      local_120._0_16_ = auVar35;
      auVar46 = vshufps_avx(ZEXT416((uint)fVar47),ZEXT416((uint)fVar47),0);
      local_140._16_16_ = auVar46;
      local_140._0_16_ = auVar46;
      auVar56 = ZEXT1264(ZEXT812(0));
      uVar24 = local_f4 + 3;
      if (uVar24 < local_ec - 3U) {
        do {
          lVar21 = lVar1 + (ulonglong)(uVar24 * local_200 >> 1) * 2;
          uVar23 = (ulonglong)(local_f8 + 3U);
          uVar13 = (iVar11 - local_f8) - 6U & 0x80000007;
          if ((int)uVar13 < 0) {
            uVar13 = (uVar13 - 1 | 0xfffffff8) + 1;
          }
          if (local_f8 + 3U < (iVar11 - uVar13) - 3) {
            do {
              auVar52 = *(undefined1 (*) [16])(lVar21 + uVar23 * 2);
              auVar51 = *(undefined1 (*) [16])
                         (lVar2 + (ulonglong)(uVar24 * local_1f8 >> 1) * 2 + uVar23 * 2);
              auVar50 = vpunpckhwd_avx(auVar51,ZEXT416(0));
              auVar51 = vpunpcklwd_avx(auVar51,ZEXT416(0));
              auVar33._16_16_ = auVar50;
              auVar33._0_16_ = auVar51;
              auVar32 = vcvtdq2ps_avx(auVar33);
              auVar51 = vpunpckhwd_avx(auVar52,ZEXT416(0));
              auVar52 = vpunpcklwd_avx(auVar52,ZEXT416(0));
              auVar34._16_16_ = auVar51;
              auVar34._0_16_ = auVar52;
              auVar37 = vcvtdq2ps_avx(auVar34);
              auVar29._0_4_ = auVar32._0_4_ * auVar46._0_4_ + auVar37._0_4_ * auVar35._0_4_;
              auVar29._4_4_ = auVar32._4_4_ * auVar46._4_4_ + auVar37._4_4_ * auVar35._4_4_;
              auVar29._8_4_ = auVar32._8_4_ * auVar46._8_4_ + auVar37._8_4_ * auVar35._8_4_;
              auVar29._12_4_ = auVar32._12_4_ * auVar46._12_4_ + auVar37._12_4_ * auVar35._12_4_;
              auVar29._16_4_ = auVar32._16_4_ * auVar46._0_4_ + auVar37._16_4_ * auVar35._0_4_;
              auVar29._20_4_ = auVar32._20_4_ * auVar46._4_4_ + auVar37._20_4_ * auVar35._4_4_;
              auVar29._24_4_ = auVar32._24_4_ * auVar46._8_4_ + auVar37._24_4_ * auVar35._8_4_;
              auVar29._28_4_ = auVar32._28_4_ * auVar46._12_4_ + auVar37._28_4_ * auVar35._12_4_;
              auVar32 = vmaxps_avx(auVar56._0_32_,auVar29);
              auVar32 = vminps_avx(_DAT_140468ca0,auVar32);
              auVar32 = vcvtps2dq_avx(auVar32);
              auVar52 = vpackusdw_avx(auVar32._0_16_,auVar32._16_16_);
              *(undefined1 (*) [16])(lVar21 + uVar23 * 2) = auVar52;
              uVar25 = (int)uVar23 + 8;
              uVar23 = (ulonglong)uVar25;
              uVar13 = (local_f0 - local_f8) - 6U & 0x80000007;
              if ((int)uVar13 < 0) {
                uVar13 = (uVar13 - 1 | 0xfffffff8) + 1;
              }
              iVar11 = local_f0;
            } while (uVar25 < (local_f0 - uVar13) - 3);
          }
          if ((uint)uVar23 < iVar11 - 3U) {
            do {
              lVar21 = (ulonglong)(uVar24 * *(int *)(lVar17 + 0x14)) + *(longlong *)(lVar17 + 0x20);
              fVar30 = (float)(int)*(short *)((ulonglong)
                                              (uVar24 * *(int *)((longlong)puVar18 + 0x14)) +
                                              puVar18[4] + uVar23 * 2) * fVar47 +
                       (float)*(ushort *)(lVar21 + uVar23 * 2) * fVar3;
              if (0.0 <= fVar30) {
                uVar10 = 0x7fff;
                if (fVar30 <= fVar28) {
                  uVar10 = (undefined2)(int)fVar30;
                }
              }
              else {
                uVar10 = 0;
              }
              *(undefined2 *)(lVar21 + uVar23 * 2) = uVar10;
              uVar13 = (int)uVar23 + 1;
              uVar23 = (ulonglong)uVar13;
            } while (uVar13 < local_f0 - 3U);
            auVar56 = ZEXT864(0) << 0x20;
            iVar11 = local_f0;
          }
          uVar24 = uVar24 + 1;
        } while (uVar24 < local_ec - 3U);
      }
      local_258 = CONCAT44(local_258._4_4_,4);
      (*(code *)local_100[0xb])(&local_100,4,4,4);
      *(int *)(local_160 + 0x30) = local_f8;
      *(int *)(local_160 + 0x38) = local_f0;
      *(int *)(local_160 + 0x34) = local_f4;
      *(int *)(local_160 + 0x3c) = local_ec;
      (**(code **)*puVar18)(puVar18,1);
    }
    else {
      if (puVar18 != (undefined8 *)0x0) {
        (**(code **)*puVar18)(puVar18,1);
      }
      puVar15 = (undefined8 *)0x6;
    }
  }
  return puVar15;
}



// ===== depth0 FUN_140359390 @ 0x140359390 rva=0x359390 size=689 =====

uint FUN_140359390(undefined8 param_1,longlong param_2,uint *param_3,uint *param_4,uint param_5,
                  int param_6,longlong param_7)

{
  ushort uVar1;
  ushort uVar2;
  ushort uVar3;
  ushort uVar4;
  ushort uVar5;
  ushort uVar6;
  ushort uVar7;
  ushort uVar8;
  ushort uVar9;
  uint uVar10;
  longlong lVar11;
  int iVar12;
  int iVar13;
  int iVar14;
  int iVar15;
  int iVar16;
  int iVar17;
  int iVar18;
  int iVar19;
  uint uVar20;
  int iVar21;
  
  lVar11 = (ulonglong)param_5 * 2;
  uVar1 = *(ushort *)
           ((ulonglong)(uint)((param_6 + -2) * *(int *)(param_2 + 0x14)) + lVar11 +
           *(longlong *)(param_2 + 0x20));
  *param_3 = (uint)uVar1;
  *param_4 = (uint)uVar1;
  uVar2 = *(ushort *)
           ((ulonglong)(uint)((param_6 + -1) * *(int *)(param_2 + 0x14)) + lVar11 +
           *(longlong *)(param_2 + 0x20));
  uVar20 = *param_3;
  if ((int)(uint)uVar2 <= (int)*param_3) {
    uVar20 = (uint)uVar2;
  }
  *param_3 = uVar20;
  uVar20 = (uint)uVar2;
  if ((int)(uint)uVar2 <= (int)*param_4) {
    uVar20 = *param_4;
  }
  *param_4 = uVar20;
  uVar3 = *(ushort *)
           ((ulonglong)(uint)(param_6 * *(int *)(param_2 + 0x14)) + (ulonglong)(param_5 - 2) * 2 +
           *(longlong *)(param_2 + 0x20));
  uVar20 = *param_3;
  if ((int)(uint)uVar3 <= (int)*param_3) {
    uVar20 = (uint)uVar3;
  }
  *param_3 = uVar20;
  uVar20 = (uint)uVar3;
  if ((int)(uint)uVar3 <= (int)*param_4) {
    uVar20 = *param_4;
  }
  *param_4 = uVar20;
  uVar4 = *(ushort *)
           ((ulonglong)(uint)(param_6 * *(int *)(param_2 + 0x14)) + (ulonglong)(param_5 - 1) * 2 +
           *(longlong *)(param_2 + 0x20));
  uVar20 = *param_3;
  if ((int)(uint)uVar4 <= (int)uVar20) {
    uVar20 = (uint)uVar4;
  }
  *param_3 = uVar20;
  uVar20 = (uint)uVar4;
  if ((int)(uint)uVar4 <= (int)*param_4) {
    uVar20 = *param_4;
  }
  *param_4 = uVar20;
  uVar5 = *(ushort *)
           ((ulonglong)(uint)(param_6 * *(int *)(param_2 + 0x14)) + lVar11 +
           *(longlong *)(param_2 + 0x20));
  uVar20 = *param_3;
  if ((int)(uint)uVar5 <= (int)*param_3) {
    uVar20 = (uint)uVar5;
  }
  *param_3 = uVar20;
  uVar20 = (uint)uVar5;
  if ((int)(uint)uVar5 <= (int)*param_4) {
    uVar20 = *param_4;
  }
  *param_4 = uVar20;
  uVar6 = *(ushort *)
           ((ulonglong)(uint)(param_6 * *(int *)(param_2 + 0x14)) + (ulonglong)(param_5 + 1) * 2 +
           *(longlong *)(param_2 + 0x20));
  uVar20 = *param_3;
  if ((int)(uint)uVar6 <= (int)uVar20) {
    uVar20 = (uint)uVar6;
  }
  *param_3 = uVar20;
  uVar20 = (uint)uVar6;
  if ((int)(uint)uVar6 <= (int)*param_4) {
    uVar20 = *param_4;
  }
  *param_4 = uVar20;
  uVar7 = *(ushort *)
           ((ulonglong)(uint)(param_6 * *(int *)(param_2 + 0x14)) + (ulonglong)(param_5 + 2) * 2 +
           *(longlong *)(param_2 + 0x20));
  uVar20 = *param_3;
  if ((int)(uint)uVar7 <= (int)*param_3) {
    uVar20 = (uint)uVar7;
  }
  *param_3 = uVar20;
  uVar20 = (uint)uVar7;
  if ((int)(uint)uVar7 <= (int)*param_4) {
    uVar20 = *param_4;
  }
  *param_4 = uVar20;
  uVar8 = *(ushort *)
           ((ulonglong)(uint)((param_6 + 1) * *(int *)(param_2 + 0x14)) + lVar11 +
           *(longlong *)(param_2 + 0x20));
  uVar20 = *param_3;
  if ((int)(uint)uVar8 <= (int)*param_3) {
    uVar20 = (uint)uVar8;
  }
  *param_3 = uVar20;
  uVar20 = (uint)uVar8;
  if ((int)(uint)uVar8 <= (int)*param_4) {
    uVar20 = *param_4;
  }
  *param_4 = uVar20;
  uVar9 = *(ushort *)
           ((ulonglong)(uint)((param_6 + 2) * *(int *)(param_2 + 0x14)) + lVar11 +
           *(longlong *)(param_2 + 0x20));
  uVar20 = *param_3;
  if ((int)(uint)uVar9 <= (int)*param_3) {
    uVar20 = (uint)uVar9;
  }
  *param_3 = uVar20;
  uVar20 = (uint)uVar9;
  if ((int)(uint)uVar9 <= (int)*param_4) {
    uVar20 = *param_4;
  }
  *param_4 = uVar20;
  uVar10 = *param_3;
  iVar12 = (int)(uVar20 - uVar10) / 2;
  if (uVar20 - uVar10 < (uint)(longlong)*(float *)(param_7 + 8)) {
    uVar20 = 0;
  }
  else {
    iVar21 = 0x100;
    if ((int)(uVar9 - uVar10) < iVar12) {
      iVar21 = 0;
    }
    iVar13 = 0x80;
    if ((int)(uVar8 - uVar10) < iVar12) {
      iVar13 = 0;
    }
    iVar14 = 0x40;
    if ((int)(uVar7 - uVar10) < iVar12) {
      iVar14 = 0;
    }
    iVar15 = 0x10;
    if ((int)(uVar5 - uVar10) < iVar12) {
      iVar15 = 0;
    }
    iVar16 = 4;
    if ((int)(uVar3 - uVar10) < iVar12) {
      iVar16 = 0;
    }
    iVar17 = 2;
    if ((int)(uVar2 - uVar10) < iVar12) {
      iVar17 = 0;
    }
    iVar18 = 8;
    if ((int)(uVar4 - uVar10) < iVar12) {
      iVar18 = 0;
    }
    iVar19 = 0x20;
    if ((int)(uVar6 - uVar10) < iVar12) {
      iVar19 = 0;
    }
    uVar20 = iVar19 + iVar21 + iVar13 + iVar14 + iVar15 + iVar16 +
                      (uint)(iVar12 <= (int)(uVar1 - uVar10)) + iVar17 + iVar18;
    if (uVar20 >> 8 != 0) {
      uVar20 = ~uVar20 & 0xff;
    }
  }
  return uVar20;
}



// ===== depth0 FUN_1403599a0 @ 0x1403599a0 rva=0x3599a0 size=860 =====

void FUN_1403599a0(void)

{
  return;
}



// ===== depth0 FUN_14035a1d0 @ 0x14035a1d0 rva=0x35a1d0 size=128 =====

float FUN_14035a1d0(float param_1,float param_2,float param_3,float param_4,float param_5,
                   float param_6,float param_7)

{
  float fVar1;
  
  if (param_1 <= param_7) {
    if (param_7 < param_2) {
      fVar1 = (param_2 - param_7) / (param_2 - param_1);
      return (DAT_1404debbc - fVar1) * param_6 + fVar1 * param_5;
    }
    if (param_7 < param_3) {
      return param_6;
    }
    if (param_7 < param_4) {
      fVar1 = (param_4 - param_7) / (param_4 - param_3);
      return (DAT_1404debbc - fVar1) * param_5 + fVar1 * param_6;
    }
  }
  return param_5;
}



// ===== depth0 FUN_14035a600 @ 0x14035a600 rva=0x35a600 size=902 =====

void FUN_14035a600(longlong param_1,longlong param_2,uint param_3)

{
  uint uVar1;
  uint uVar2;
  float fVar3;
  float fVar4;
  float fVar5;
  float fVar6;
  float fVar7;
  float fVar8;
  float fVar9;
  float fVar10;
  
  fVar9 = DAT_1404df200;
  uVar1 = *(uint *)(param_2 + 0xb4);
  if (param_3 < uVar1) {
    fVar10 = (float)*(int *)(param_2 + 0x70) * DAT_1404df200;
    *(float *)(param_1 + 0x84) = fVar10;
    fVar3 = *(float *)(param_2 + 0xa8);
  }
  else {
    uVar2 = *(uint *)(param_2 + 0xb8);
    if (param_3 < uVar2) {
      fVar3 = (float)(param_3 - uVar1) / (float)(int)(uVar2 - uVar1);
      fVar5 = DAT_1404debbc - fVar3;
      fVar10 = ((float)*(int *)(param_2 + 0x70) * fVar5 + (float)*(int *)(param_2 + 0x74) * fVar3) *
               DAT_1404df200;
      *(float *)(param_1 + 0x84) = fVar10;
      fVar3 = fVar5 * *(float *)(param_2 + 0xa8) + fVar3 * *(float *)(param_2 + 0xac);
    }
    else if (param_3 < *(uint *)(param_2 + 0xbc)) {
      fVar3 = (float)(param_3 - uVar2) / (float)(int)(*(uint *)(param_2 + 0xbc) - uVar2);
      fVar5 = DAT_1404debbc - fVar3;
      fVar10 = ((float)*(int *)(param_2 + 0x74) * fVar5 + (float)*(int *)(param_2 + 0x78) * fVar3) *
               DAT_1404df200;
      *(float *)(param_1 + 0x84) = fVar10;
      fVar3 = fVar5 * *(float *)(param_2 + 0xac) + fVar3 * *(float *)(param_2 + 0xb0);
    }
    else {
      fVar10 = (float)*(int *)(param_2 + 0x78) * DAT_1404df200;
      *(float *)(param_1 + 0x84) = fVar10;
      fVar3 = *(float *)(param_2 + 0xb0);
    }
  }
  *(float *)(param_1 + 0xc4) = fVar3;
  fVar7 = (float)*(int *)(param_2 + 0x40) * fVar9;
  *(float *)(param_1 + 0x44) = fVar7;
  fVar8 = (float)*(int *)(param_2 + 0x44) * fVar9;
  *(float *)(param_1 + 0x4c) = fVar8;
  fVar6 = (float)*(int *)(param_2 + 0x48);
  *(float *)(param_1 + 0x54) = fVar6;
  fVar4 = (float)*(int *)(param_2 + 0x4c);
  *(float *)(param_1 + 0x58) = fVar4;
  fVar3 = *(float *)(param_2 + 0x50);
  *(float *)(param_1 + 0x5c) = fVar3;
  fVar5 = *(float *)(param_2 + 0x54);
  *(float *)(param_1 + 0x60) = fVar5;
  *(float *)(param_1 + 0x48) = (fVar4 - fVar6) / fVar3 + fVar7;
  *(float *)(param_1 + 0x50) = fVar8 - (fVar6 - fVar4) / fVar5;
  fVar7 = (float)*(int *)(param_2 + 0x58) * fVar9;
  *(float *)(param_1 + 100) = fVar7;
  fVar8 = (float)*(int *)(param_2 + 0x5c) * fVar9;
  *(float *)(param_1 + 0x6c) = fVar8;
  fVar6 = (float)*(int *)(param_2 + 0x60);
  *(float *)(param_1 + 0x74) = fVar6;
  fVar4 = (float)*(int *)(param_2 + 100);
  *(float *)(param_1 + 0x78) = fVar4;
  fVar3 = *(float *)(param_2 + 0x68);
  *(float *)(param_1 + 0x7c) = fVar3;
  fVar5 = *(float *)(param_2 + 0x6c);
  *(float *)(param_1 + 0x80) = fVar5;
  *(float *)(param_1 + 0x68) = (fVar4 - fVar6) / fVar3 + fVar7;
  *(float *)(param_1 + 0x70) = fVar8 - (fVar6 - fVar4) / fVar5;
  fVar7 = (float)*(int *)(param_2 + 0x7c) * fVar9;
  *(float *)(param_1 + 0x8c) = fVar7;
  fVar6 = (float)*(int *)(param_2 + 0x80);
  *(float *)(param_1 + 0x94) = fVar6;
  fVar4 = (float)*(int *)(param_2 + 0x84);
  *(float *)(param_1 + 0x98) = fVar4;
  fVar3 = *(float *)(param_2 + 0x88);
  *(float *)(param_1 + 0x9c) = fVar3;
  fVar5 = *(float *)(param_2 + 0x8c);
  *(float *)(param_1 + 0xa0) = fVar5;
  *(float *)(param_1 + 0x88) = (fVar4 - fVar6) / fVar3 + fVar10;
  *(float *)(param_1 + 0x90) = fVar7 - (fVar6 - fVar4) / fVar5;
  fVar6 = (float)*(int *)(param_2 + 0x90) * fVar9;
  *(float *)(param_1 + 0xa4) = fVar6;
  fVar9 = (float)*(int *)(param_2 + 0x94) * fVar9;
  *(float *)(param_1 + 0xac) = fVar9;
  fVar4 = (float)*(int *)(param_2 + 0x98);
  *(float *)(param_1 + 0xb4) = fVar4;
  fVar5 = (float)*(int *)(param_2 + 0x9c);
  *(float *)(param_1 + 0xb8) = fVar5;
  fVar3 = *(float *)(param_2 + 0xa0);
  *(float *)(param_1 + 0xbc) = fVar3;
  fVar10 = *(float *)(param_2 + 0xa4);
  *(float *)(param_1 + 0xc0) = fVar10;
  *(float *)(param_1 + 0xa8) = (fVar5 - fVar4) / fVar3 + fVar6;
  *(float *)(param_1 + 0xb0) = fVar9 - (fVar4 - fVar5) / fVar10;
  return;
}



