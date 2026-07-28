// 1 functions, roots=0x36e920 depth=0

// ===== depth0 FUN_14036e920 @ 0x14036e920 rva=0x36e920 size=1289 =====

/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

undefined8 FUN_14036e920(undefined8 param_1,longlong param_2,undefined8 *param_3)

{
  short sVar1;
  uint uVar2;
  int iVar3;
  longlong lVar4;
  int iVar5;
  longlong lVar6;
  longlong lVar7;
  longlong lVar8;
  longlong *plVar9;
  longlong lVar10;
  undefined8 uVar11;
  uint uVar12;
  ulonglong uVar13;
  int iVar14;
  short sVar15;
  int iVar16;
  short *psVar17;
  int iVar18;
  ushort *puVar19;
  int iVar20;
  ushort *puVar21;
  double dVar22;
  double dVar23;
  double dVar24;
  double dVar25;
  double dVar26;
  double dVar27;
  int local_res10;
  
  uVar11 = *(undefined8 *)(param_2 + 8);
  lVar6 = FUN_140152710(uVar11,0);
  lVar7 = FUN_140152710(uVar11,1);
  lVar8 = FUN_140152710(uVar11,2);
  uVar2 = *(uint *)(lVar6 + 8);
  iVar3 = *(int *)(lVar6 + 0xc);
  plVar9 = (longlong *)
           __RTDynamicCast(*param_3,0,&sony_zhacai::IZcImage::RTTI_Type_Descriptor,
                           &sony_zhacai::ZcARW::RTTI_Type_Descriptor,0);
  lVar10 = (**(code **)(*plVar9 + 0xd8))(plVar9,*(undefined4 *)(param_3[1] + 0x1e0));
  iVar18 = *(int *)(lVar10 + 0x2c);
  iVar14 = (int)*(short *)(lVar10 + 0xf06);
  iVar20 = (int)*(short *)(lVar10 + 0xf00);
  sVar1 = *(short *)(lVar10 + 0xf0c);
  if (((((iVar18 == 0x100) || (iVar18 == 0x102)) || (iVar18 == 0x103)) ||
      ((iVar18 == 0x104 || (uVar12 = iVar18 - 0x100, iVar18 == 0x105)))) ||
     (dVar27 = (double)iVar20, uVar12 < 0x16)) {
                    /* WARNING: Could not recover jumptable at 0x00014036ea9a. Too many branches */
                    /* WARNING: Treating indirect jump as call */
    uVar11 = (*(code *)(IMAGE_DOS_HEADER_140000000.e_magic +
                       *(uint *)(&DAT_14036eeac + (longlong)(iVar18 + -0x100) * 4)))
                       (IMAGE_DOS_HEADER_140000000.e_magic +
                        *(uint *)(&DAT_14036eeac + (longlong)(iVar18 + -0x100) * 4));
    return uVar11;
  }
  dVar25 = (double)(int)*(short *)(lVar10 + 0xf02);
                    /* WARNING: Could not find normalized switch variable to match jumptable */
  switch(uVar12) {
  case 0:
  case 1:
  case 2:
  case 3:
  case 4:
  case 5:
  case 6:
  case 7:
  case 8:
  case 9:
  case 10:
  case 0xd:
  case 0xe:
  case 0x11:
  case 0x12:
  case 0x13:
  case 0x15:
    dVar24 = DAT_140467698;
                    /* WARNING: This code block may not be properly labeled as switch case */
LAB_14036eade:
    dVar22 = DAT_140467508;
    switch(IMAGE_DOS_HEADER_140000000.e_magic +
           (&switchD_14036eaec::switchdataD_14036ef04)[(int)uVar12]) {
    case (char *)0x14036eaee:
      dVar22 = DAT_140467680;
      break;
    case (char *)0x14036eaf8:
      dVar22 = DAT_140467670;
      break;
    case (char *)0x14036eb02:
      dVar22 = DAT_1404def40;
      break;
    case (char *)0x14036eb0c:
      dVar22 = DAT_1404def68;
      break;
    case (char *)0x14036eb11:
      goto switchD_14036eaec_caseD_4036eb11;
    }
switchD_14036eaec_caseD_4036eb26:
    dVar23 = DAT_140467698;
    switch(IMAGE_DOS_HEADER_140000000.e_magic +
           (&switchD_14036eb34::switchdataD_14036ef5c)[(int)uVar12]) {
    case (char *)0x14036eb36:
      dVar23 = DAT_140467690;
      break;
    case (char *)0x14036eb40:
      goto switchD_14036eb34_caseD_4036eb40;
    }
    break;
  default:
                    /* WARNING: This code block may not be properly labeled as switch case */
    dVar24 = (double)(int)*(short *)(lVar10 + 0xf08);
    if (uVar12 < 0x16) goto LAB_14036eade;
switchD_14036eaec_caseD_4036eb11:
    dVar22 = (double)(int)*(short *)(lVar10 + 0xf04);
    if (uVar12 < 0x16) goto switchD_14036eaec_caseD_4036eb26;
switchD_14036eb34_caseD_4036eb40:
    dVar23 = (double)(int)*(short *)(lVar10 + 0xf0a);
  }
  lVar4 = *(longlong *)(*(longlong *)(param_2 + 0x68) + 200);
  dVar26 = (double)*(int *)(lVar4 + 0xc) / _DAT_1404defb8;
  iVar18 = iVar14;
  if ((DAT_1404df2e8 <= dVar26) && (dVar26 < DAT_1404df2e0)) {
    iVar20 = (int)((dVar25 - dVar22) * (dVar26 - DAT_1404df2e8) + dVar22);
    iVar18 = (int)((dVar24 - dVar23) * (dVar26 - DAT_1404df2e8) + dVar23);
  }
  if ((DAT_1404df2e0 <= dVar26) && (dVar26 < 0.0)) {
    iVar18 = (int)(((double)iVar14 - dVar24) * (dVar26 - DAT_1404df2e0) + dVar24);
    iVar20 = (int)((dVar27 - dVar25) * (dVar26 - DAT_1404df2e0) + dVar25);
  }
  iVar14 = iVar18;
  if (0x3fff < iVar18) {
    iVar14 = 0x3fff;
  }
  dVar27 = (double)(int)*(short *)(lVar4 + 0x18ee) * DAT_1404debf0;
  iVar14 = (int)((double)(int)*(short *)(lVar4 + 0x79962 +
                                        (longlong)*(short *)(lVar4 + 0x89962 + (longlong)iVar14 * 2)
                                        * 2) * dVar27);
  if (0x7fff < iVar14) {
    iVar14 = 0x7fff;
  }
  iVar14 = (int)*(short *)(lVar4 + 0x318fc +
                          (longlong)*(short *)(lVar4 + 0x118f6 + (longlong)iVar14 * 2) * 2);
  if (iVar18 == 0) {
    iVar18 = 0;
  }
  else {
    iVar18 = (iVar14 * *(short *)(lVar10 + 0xf0e)) / iVar18;
  }
  dVar25 = 0.0;
  if ((DAT_1404df2e8 <= dVar26) && (dVar26 <= DAT_1404df2e0)) {
    dVar25 = dVar26 - DAT_1404df2e8;
  }
  if ((DAT_1404df2e0 <= dVar26) && (dVar26 <= 0.0)) {
    dVar25 = (double)CONCAT44((uint)((ulonglong)dVar26 >> 0x20) ^ DAT_1404e02c0._4_4_,
                              SUB84(dVar26,0) ^ (uint)DAT_1404e02c0);
  }
  dVar24 = dVar25 * _DAT_140467688;
  local_res10 = 0;
  iVar14 = iVar14 + (int)(dVar25 * _DAT_1404676a0);
  if (0 < iVar3) {
    do {
      puVar19 = (ushort *)
                ((ulonglong)(uint)(local_res10 * *(int *)(lVar7 + 0x14)) +
                *(longlong *)(lVar7 + 0x20));
      puVar21 = (ushort *)
                ((ulonglong)(uint)(local_res10 * *(int *)(lVar8 + 0x14)) +
                *(longlong *)(lVar8 + 0x20));
      psVar17 = (short *)((ulonglong)(uint)(local_res10 * *(int *)(lVar6 + 0x14)) +
                         *(longlong *)(lVar6 + 0x20));
      if (0 < (int)uVar2) {
        uVar13 = (ulonglong)uVar2;
        do {
          sVar15 = *psVar17;
          iVar16 = (int)sVar15;
          iVar5 = 0xff;
          if (iVar16 < iVar18) {
            iVar5 = (iVar18 - iVar16) * (int)((double)(int)sVar1 / dVar27);
LAB_14036ed9d:
            iVar5 = 0xff - (iVar5 >> 0xc);
            if (iVar5 < 0) {
              iVar5 = 0;
            }
            if (0xff < iVar5) {
              iVar5 = 0xff;
            }
          }
          else if (iVar14 < iVar16) {
            iVar5 = (iVar16 - iVar14) * ((int)((double)iVar20 / dVar27) + (int)dVar24);
            goto LAB_14036ed9d;
          }
          if (sVar15 < 0) {
            sVar15 = 0;
          }
          *psVar17 = sVar15;
          psVar17 = psVar17 + 1;
          iVar16 = (*puVar19 - 0x8000) * iVar5;
          *puVar19 = (short)(iVar16 + (iVar16 >> 0x1f & 0xffU) >> 8) + 0x8000;
          puVar19 = puVar19 + 1;
          iVar5 = (*puVar21 - 0x8000) * iVar5;
          *puVar21 = (short)(iVar5 + (iVar5 >> 0x1f & 0xffU) >> 8) + 0x8000;
          puVar21 = puVar21 + 1;
          uVar13 = uVar13 - 1;
        } while (uVar13 != 0);
      }
      local_res10 = local_res10 + 1;
    } while (local_res10 < iVar3);
  }
  return 0;
}



